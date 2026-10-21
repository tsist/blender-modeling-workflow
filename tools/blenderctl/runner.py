"""Modeling-only supervisor retaining public jobs, ownership and input guards.

Derived from the public Blender CLI. Unrelated tool/transaction dispatch is not
part of this distribution. Cloud calls never require a Blender executable.
"""
from contextlib import ExitStack
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import traceback
import uuid

from protocol import (CODES, DEFAULT_BLENDER, DEFAULT_JOBS, DEFAULT_TRANSACTIONS,
                      Failure, atomic_json, digest, envelope, read_json,
                      timeout_value, validate)
from processes import ProcessTree


def require_windows():
    if os.name != 'nt':
        raise Failure('UNSUPPORTED', 'This release requires Windows file guards and owned process trees')


def job_path(root, job_id):
    if not isinstance(job_id, str) or not re.fullmatch(r'[0-9a-f]{32}', job_id):
        raise Failure('INVALID_REQUEST', 'job ID must be 32 lowercase hex characters')
    root = Path(root).resolve()
    path = (root / job_id).resolve()
    if path.parent != root:
        raise Failure('INVALID_REQUEST', 'Job path leaves jobs directory')
    if not path.is_dir():
        raise Failure('NOT_FOUND', f'Unknown job: {job_id}')
    return path


def prepare(request, blender=DEFAULT_BLENDER, root=DEFAULT_JOBS, timeout=120,
            transactions_root=DEFAULT_TRANSACTIONS, internal=False, limits=None,
            resource_domain=None):
    require_windows()
    if internal or limits is not None or resource_domain is not None:
        raise Failure('UNSUPPORTED', 'Only public modeling requests without generic resource domains are supported')
    request = validate(request)
    timeout_value(timeout)
    blender = Path(blender).resolve()
    local = request['params']['manifest']['operation'] == 'import_local'
    if local and not blender.is_file():
        raise Failure('NOT_FOUND', f'Blender executable not found: {blender}; select --blender explicitly')
    job = Path(root).resolve() / uuid.uuid4().hex
    job.mkdir(parents=True, exist_ok=False)
    atomic_json(job / 'request.json', request)
    atomic_json(job / 'launch.json', {'blender': str(blender), 'timeout_seconds': timeout,
                                     'request_sha256': digest(job / 'request.json'),
                                     'transactions_root': str(Path(transactions_root).resolve())})
    atomic_json(job / 'status.json', {'job_id': job.name, 'state': 'queued',
                                     'created_at': time.time(), 'heartbeat_at': time.time()})
    return job


def submit(job):
    require_windows()
    flags = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS
    with (job / 'supervisor.log').open('ab') as log:
        subprocess.Popen([sys.executable, str(Path(__file__).resolve()), str(job)],
                         stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                         creationflags=flags, close_fds=True)
    return envelope('job.submit', job.name, {'state': 'queued'}, artifacts={'job_directory': str(job)})


def status(job):
    value = read_json(job / 'status.json')
    value['health'] = ('stale_supervisor_review_required'
                       if value['state'] in ('queued', 'running') and time.time() - value['heartbeat_at'] > 15
                       else 'current')
    return envelope('job.status', job.name, value, artifacts={'job_directory': str(job)})


def wait_for_job(job, seconds=30):
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not 0 <= seconds <= 55:
        raise Failure('INVALID_REQUEST', 'Job wait must be 0..55 seconds')
    deadline = time.monotonic() + seconds
    while True:
        if (job / 'result.json').is_file():
            return read_json(job / 'result.json')
        observed = status(job)
        if observed['data']['state'] not in ('queued', 'running') or time.monotonic() >= deadline:
            return observed
        time.sleep(min(.25, max(0, deadline - time.monotonic())))


def cancel(job):
    require_windows()
    value = status(job)['data']
    if value['state'] in ('queued', 'running'):
        (job / 'cancel.request').touch(exist_ok=True)
        value['cancel_requested'] = True
    return envelope('job.cancel', job.name, value, artifacts={'job_directory': str(job)})


def _lifecycle(job, parent_checkpoint):
    launch = read_json(job / 'launch.json')
    timeout_value(launch['timeout_seconds'])
    state = read_json(job / 'status.json')
    if state['state'] != 'queued':
        raise Failure('CONFLICT', 'Only a queued modeling job may execute; never replay a financial job')
    started = time.monotonic()
    state.update(state='running', supervisor_pid=os.getpid(), started_at=time.time())
    atomic_json(job / 'status.json', state)

    def checkpoint():
        if parent_checkpoint:
            parent_checkpoint()
        if (job / 'cancel.request').exists():
            raise Failure('CANCELLED', 'Modeling job cancelled; reservations and partial evidence retained')
        if time.monotonic() - started > launch['timeout_seconds']:
            raise Failure('TIMEOUT', 'Modeling job exceeded timeout; reservations and partial evidence retained')
        if time.time() - state['heartbeat_at'] >= 1:
            state['heartbeat_at'] = time.time()
            atomic_json(job / 'status.json', state)
    return launch, state, started, checkpoint


def _finish(job, state, started, data, error, artifacts, worker_exit=None):
    code = CODES.get(error['code'], CODES['WORKER_FAILED']) if error else 0
    state.update(state='cancelled' if code == 7 else 'timed_out' if code == 6 else 'failed' if code else 'succeeded',
                 exit_code=code, finished_at=time.time(), heartbeat_at=time.time(),
                 elapsed_seconds=round(time.monotonic() - started, 3))
    if worker_exit is not None:
        state['worker_exit_code'] = worker_exit
    for path in job.rglob('*'):
        if path.is_file() and (path.name.startswith('modeling-') or path.suffix.lower() in ('.blend', '.png')):
            artifacts[str(path.relative_to(job))] = str(path)
    result = envelope('modeling.run', job.name, data if not error else None, error, artifacts)
    atomic_json(job / 'result.json', result)
    atomic_json(job / 'status.json', state)
    return result, code


def _failure(exc, job):
    if isinstance(exc, KeyboardInterrupt):
        return {'code': 'CANCELLED', 'message': 'Interrupted; reservations and completed evidence retained'}
    if isinstance(exc, Failure):
        return {'code': exc.code, 'message': str(exc)}
    if isinstance(exc, OSError):
        return {'code': 'IO_ERROR', 'message': str(exc)}
    (job / 'supervisor-error.log').write_text(traceback.format_exc(), encoding='utf8')
    return {'code': 'WORKER_FAILED', 'message': str(exc)}


def execute_worker(job, parent_checkpoint=None):
    from timing import Trace
    trace = Trace(job / 'timing.json', metadata={'scope': 'host worker lifecycle; render stages have a separate trace',
                                               'job_id': job.name, 'parent_job_id': None})
    try:
        with trace.span('worker_job_total') as span:
            result, code = _execute_worker(job, parent_checkpoint)
        error = result.get('error')
        if error:
            span['status'] = 'failed'
            span['error'] = error
        trace.finish(Failure(error['code'], error['message']) if error else None)
        result['artifacts']['timing'] = str(job / 'timing.json')
        atomic_json(job / 'result.json', result)
        return result, code
    except BaseException as exc:
        trace.finish(exc)
        raise


def _execute_worker(job, parent_checkpoint=None):
    from filesystem import FileGuard
    from modeling_workflow_contract import input_documents
    request = validate(read_json(job / 'request.json'))
    if request['params']['manifest']['operation'] != 'import_local':
        raise Failure('INVALID_REQUEST', 'Only local import may launch Blender')
    launch, state, started, checkpoint = _lifecycle(job, parent_checkpoint)
    guards = ExitStack()
    hashes = {}
    tree = None
    data = error = None
    worker_exit = None
    artifacts = {'job_directory': str(job), 'request': str(job / 'request.json'),
                 'stdout_log': str(job / 'stdout.log'), 'stderr_log': str(job / 'stderr.log')}
    try:
        checkpoint()
        for document in input_documents(request['params']):
            filename = document['file']
            if filename not in hashes:
                guard = guards.enter_context(FileGuard(filename))
                hashes[filename] = guard.sha256(checkpoint)
            if hashes[filename] != document['expected_sha256']:
                raise Failure('CONFLICT', 'Modeling immutable input changed before worker launch')
        config = job / 'user'
        env = {key: value for key, value in os.environ.items()
               if not key.startswith('BLENDER_') and key not in ('PYTHONPATH', 'PYTHONHOME', 'PYTHONSTARTUP')}
        for name in ('CONFIG', 'SCRIPTS', 'EXTENSIONS', 'DATAFILES'):
            path = config / name.lower()
            path.mkdir(parents=True)
            env['BLENDER_USER_' + name] = str(path)
        env['BLENDER_USER_RESOURCES'] = str(config)
        env['PYTHONIOENCODING'] = 'utf-8'
        env['PYTHONUNBUFFERED'] = '1'
        command = [launch['blender'], '--background', '--factory-startup', '--disable-autoexec',
                   '--offline-mode', '--threads', '2', '--python-exit-code', '23', '--python',
                   str(Path(__file__).with_name('worker.py')), '--', str(job)]
        atomic_json(job / 'command.json', command)
        bootstrap = [sys.executable, str(Path(__file__).with_name('bootstrap.py')), str(job), *command]
        with (job / 'stdout.log').open('wb') as out, (job / 'stderr.log').open('wb') as err:
            tree = ProcessTree(bootstrap, cwd=str(job), env=env, stdin=subprocess.DEVNULL, stdout=out, stderr=err)
            state['process_tree_root_pid'] = tree.process.pid
            (job / 'worker.gate').touch(exist_ok=False)
            atomic_json(job / 'status.json', state)
            while tree.process.poll() is None:
                checkpoint()
                time.sleep(.1)
            worker_exit = tree.process.returncode
        checkpoint()
        result_file = job / 'worker-result.json'
        if worker_exit != 0 or not result_file.is_file():
            details = read_json(result_file).get('error') if result_file.is_file() else None
            code = details.get('code', 'WORKER_FAILED') if isinstance(details, dict) else 'WORKER_FAILED'
            raise Failure(code if code in CODES else 'WORKER_FAILED', f'Worker exit={worker_exit}; {details or "no result"}')
        result = read_json(result_file)
        if not result.get('ok'):
            detail = result.get('error') or {}
            raise Failure(detail.get('code') if detail.get('code') in CODES else 'WORKER_FAILED', str(detail.get('message', detail)))
        data = result['data']
        for filename, original in hashes.items():
            if digest(filename, checkpoint) != original:
                raise Failure('CONFLICT', 'Modeling immutable input changed before result acceptance')
        data['input_hashes'] = hashes
        artifacts['worker_result'] = str(result_file)
    except (KeyboardInterrupt, Exception) as exc:
        error = _failure(exc, job)
    finally:
        if tree:
            if tree.process.poll() is None:
                tree.terminate()
            worker_exit = tree.process.returncode
            tree.close()
        guards.close()
    return _finish(job, state, started, data, error, artifacts, worker_exit)


def execute(job, parent_checkpoint=None):
    require_windows()
    job = Path(job).resolve()
    from filesystem import FileGuard, PathLocks
    with PathLocks([job / 'supervisor.lock']):
        initial = read_json(job / 'status.json')
        if initial['state'] != 'queued':
            raise Failure('CONFLICT', 'Only a queued modeling job may execute; never replay a financial job')
        started = time.monotonic()
        try:
            with FileGuard(job / 'request.json') as request_guard, FileGuard(job / 'launch.json'):
                launch = read_json(job / 'launch.json')
                if request_guard.sha256() != launch.get('request_sha256'):
                    raise Failure('CONFLICT', 'Queued modeling request changed before execution')
                return _execute_owned(job, parent_checkpoint)
        except (KeyboardInterrupt, Exception) as exc:
            # Validation can fail before _lifecycle marks the job running.
            # Detached jobs must still reach a durable terminal result. Never
            # turn an already-finished financial job into another execution.
            current = read_json(job / 'status.json')
            owned_running = current['state'] == 'running' and current.get('supervisor_pid') == os.getpid()
            if current['state'] != 'queued' and not owned_running:
                raise
            current.setdefault('supervisor_pid', os.getpid())
            current.setdefault('started_at', time.time())
            return _finish(job, current, started, None, _failure(exc, job), {'job_directory': str(job)})


def _execute_owned(job, parent_checkpoint=None):
    request = validate(read_json(job / 'request.json'))
    if request['params']['manifest']['operation'] == 'import_local':
        return execute_worker(job, parent_checkpoint)
    launch, state, started, checkpoint = _lifecycle(job, parent_checkpoint)
    data = error = None
    try:
        checkpoint()
        from modeling_host import run
        data = run(request['params'], job, checkpoint)
    except (KeyboardInterrupt, Exception) as exc:
        error = _failure(exc, job)
    return _finish(job, state, started, data, error, {'job_directory': str(job)})


if __name__ == '__main__':
    _, exit_code = execute(Path(sys.argv[1]).resolve())
    raise SystemExit(exit_code)
