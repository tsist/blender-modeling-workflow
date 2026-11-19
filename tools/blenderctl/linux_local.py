"""Linux synchronous local-import supervisor. Not a provider or transaction backend."""
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import os
from pathlib import Path
import signal
import stat
import subprocess
import time
import uuid

from protocol import CODES, Failure, atomic_json, digest, envelope, read_json, timeout_value, validate

DIR = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
MAX_INPUT = 256 * 1024 * 1024


def literal(path):
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise Failure('INVALID_REQUEST', 'Expected an absolute non-traversing Linux path')
    return path


@contextmanager
def directory(path, trusted=False):
    path = literal(path)
    fd = os.open('/', DIR)
    try:
        for part in path.parts[1:]:
            nxt = os.open(part, DIR, dir_fd=fd)
            os.close(fd)
            fd = nxt
            if trusted:
                s = os.fstat(fd)
                sticky_tmp = s.st_uid == 0 and s.st_mode & stat.S_ISVTX
                if s.st_uid not in (0, os.getuid()) or (s.st_mode & 0o022 and not sticky_tmp):
                    raise Failure('CONFLICT', 'Jobs require a trusted directory ancestry')
        yield fd
    finally:
        os.close(fd)


@contextmanager
def regular(path):
    path = literal(path)
    with directory(path.parent) as parent:
        fd = os.open(path.name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC,
                     dir_fd=parent)
        with os.fdopen(fd, 'rb') as src:
            s = os.fstat(fd)
            if not stat.S_ISREG(s.st_mode) or s.st_nlink != 1:
                raise Failure('CONFLICT', 'Expected an unaliased regular input file')
            yield src


def fingerprint(src):
    s = os.fstat(src.fileno())
    return s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns


def copy_checked(src, dst, expected, tick, limit):
    before = fingerprint(src)
    if before[2] > limit:
        raise Failure('INVALID_REQUEST', 'Input exceeds size bound')
    h = hashlib.sha256()
    total = 0
    src.seek(0)
    for buf in iter(lambda: src.read(1024 * 1024), b''):
        tick()
        total += len(buf)
        if total > limit:
            raise Failure('CONFLICT', 'Input grew beyond size bound')
        if dst is not None:
            dst.write(buf)
        h.update(buf)
    if h.hexdigest() != expected or fingerprint(src) != before or total != before[2]:
        raise Failure('CONFLICT', 'Immutable input changed or SHA-256 mismatch')
    return before


def create_job(root):
    root = literal(root)
    with directory(root.parent, trusted=True) as parent:
        try:
            os.mkdir(root.name, mode=0o700, dir_fd=parent)
        except FileExistsError:
            pass
    with directory(root, trusted=True) as fd:
        s = os.fstat(fd)
        if s.st_uid != os.getuid() or s.st_mode & 0o077:
            raise Failure('CONFLICT', 'Linux jobs root must be private and owned (mode 0700)')
        name = uuid.uuid4().hex
        os.mkdir(name, 0o700, dir_fd=fd)
    return root / name


def run_group(argv, cwd, env, log, err, tick):
    # Observe without reaping: the group leader PID cannot be recycled before
    # teardown, even if the leader exits before an ordinary child.
    p = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=log,
                         stderr=err, start_new_session=True, close_fds=True)
    try:
        (cwd / 'worker.gate').touch(exist_ok=False)
        while os.waitid(os.P_PID, p.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is None:
            tick()
            time.sleep(.05)
    finally:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        p.wait()
    return p.returncode


@contextmanager
def termination_handlers():
    # The synchronous CLI owns this main thread. Defer interruption to checkpoints
    # so a signal cannot land between spawning a child and acquiring its handle.
    pending = []
    def on_signal(signum, _frame):
        pending.append(signum)
    old = {}
    try:
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            old[sig] = signal.signal(sig, on_signal)
        def check():
            if pending:
                raise Failure('CANCELLED', 'Synchronous local import interrupted')
        yield check
    finally:
        for sig, handler in old.items():
            signal.signal(sig, handler)


def local_run(request, blender, root, timeout, background):
    with termination_handlers() as interrupted:
        return _local_run(request, blender, root, timeout, background, interrupted)


def _local_run(request, blender, root, timeout, background, interrupted):
    from modeling_identity import ROOT, runtime_documents
    raw = request.get('params', {}) if isinstance(request, dict) else {}
    m = raw.get('manifest', {}) if isinstance(raw, dict) else {}
    if background or not isinstance(m, dict) or m.get('operation') != 'import_local' or m.get('provider', 'local') != 'local':
        raise Failure('UNSUPPORTED', 'Linux supports synchronous local import only; no provider or detached jobs')
    # Check the literal spelling before cross-platform normalization resolves links.
    inp = m.get('input')
    if not isinstance(inp, dict) or not isinstance(inp.get('file'), str):
        raise Failure('INVALID_REQUEST', 'Missing local GLB descriptor')
    source = literal(inp['file'])
    if source.suffix.lower() != '.glb':
        raise Failure('INVALID_REQUEST', 'Local import requires an embedded .glb')
    request = validate(request)
    normalized = request['params']['manifest']
    if normalized.get('provenance', {}).get('provider', 'local') != 'local':
        raise Failure('UNSUPPORTED', 'Linux import qualification requires local provenance')
    expected = normalized['input']['expected_sha256']
    timeout_value(timeout)
    blender = literal(blender)
    if not blender.is_file() or not os.access(blender, os.X_OK):
        raise Failure('NOT_FOUND', 'Supply a trusted Blender executable using --blender')
    job = create_job(root)
    work = job / 'worker'
    work.mkdir(mode=0o700)
    started = time.monotonic()
    heartbeat = started
    def tick():
        nonlocal heartbeat
        interrupted()
        if time.monotonic() - heartbeat >= 1:
            heartbeat = time.monotonic()
            atomic_json(job / 'status.json', {'job_id': job.name, 'state': 'running', 'heartbeat_at': time.time()})
        if time.monotonic() - started > timeout:
            raise Failure('TIMEOUT', 'Synchronous local import exceeded its deadline')

    data = error = None
    artifacts = {'job_directory': str(job), 'request': str(job / 'request.json')}
    try:
        atomic_json(job / 'status.json', {'job_id': job.name, 'state': 'running', 'heartbeat_at': time.time()})
        atomic_json(job / 'request.json', request)
        snapshot = work / 'input.glb'
        with regular(source) as src:
            with snapshot.open('xb') as dst:
                identity = copy_checked(src, dst, expected, tick, MAX_INPUT)
            # Do not run code by re-opening mutable source paths in the worker.
            runtime = work / 'runtime'
            for row in runtime_documents():
                p = Path(row['file'])
                target = runtime / p.relative_to(ROOT)
                target.parent.mkdir(parents=True, exist_ok=True)
                with regular(p) as orig, target.open('xb') as out:
                    copy_checked(orig, out, row['expected_sha256'], tick, MAX_INPUT)
            worker_request = deepcopy(request)
            worker_request['params']['manifest']['input']['file'] = str(snapshot)
            atomic_json(work / 'request.json', worker_request)
            home = work / 'home'
            home.mkdir(mode=0o700)
            env = {'PATH': os.defpath, 'HOME': str(home), 'LANG': 'C.UTF-8',
                   'BLENDER_USER_RESOURCES': str(home / 'blender'), 'PYTHONDONTWRITEBYTECODE': '1'}
            argv = [str(blender), '--background', '--factory-startup', '--disable-autoexec',
                    '--offline-mode', '--threads', '2', '--python-exit-code', '23', '--python',
                    str(runtime / 'tools/blenderctl/worker.py'), '--', str(work)]
            with (job / 'stdout.log').open('xb') as log, (job / 'stderr.log').open('xb') as err:
                rc = run_group(argv, work, env, log, err, tick)
            tick()
            result = read_json(work / 'worker-result.json') if (work / 'worker-result.json').exists() else {}
            if rc or result.get('ok') is not True:
                detail = result.get('error') or {}
                raise Failure(detail.get('code') if detail.get('code') in CODES else 'WORKER_FAILED',
                              f'Local Blender exit={rc}: {detail.get("message", "no worker receipt")}')
            data = result['data']
            candidate = work / 'output-candidate.blend'
            if (data.get('status') != 'passed' or data.get('fresh_process_reopen') != 'pass'
                    or data.get('candidate') != str(candidate) or digest(candidate, tick) != data['candidate_sha256']):
                raise Failure('VALIDATION_FAILED', 'Independent verification or candidate identity mismatch')
            with regular(source) as current:
                if fingerprint(current) != identity or copy_checked(current, None, expected, tick, MAX_INPUT) != identity:
                    raise Failure('CONFLICT', 'Original input identity changed before acceptance')
            if fingerprint(src) != identity:
                raise Failure('CONFLICT', 'Opened source changed before acceptance')
            data.update(input=request['params']['manifest']['input'], input_snapshot=str(snapshot),
                        input_hashes={str(source): expected}, source_unchanged=True)
            atomic_json(job / 'modeling-report.json', data)
            atomic_json(job / 'modeling-reopen.json', read_json(work / 'modeling-reopen.json'))
            artifacts.update(candidate=str(candidate), worker_result=str(work / 'worker-result.json'))
    except (Exception, KeyboardInterrupt) as exc:
        code = exc.code if isinstance(exc, Failure) else 'CANCELLED' if isinstance(exc, KeyboardInterrupt) else 'IO_ERROR' if isinstance(exc, OSError) else 'WORKER_FAILED'
        error = {'code': code, 'message': str(exc) or code}
    state = 'succeeded' if not error else {'TIMEOUT':'timed_out','CANCELLED':'cancelled'}.get(error['code'],'failed')
    result = envelope('modeling.run', job.name, None if error else data, error, artifacts)
    atomic_json(job / 'result.json', result)
    atomic_json(job / 'status.json', {'job_id': job.name, 'state': state, 'heartbeat_at': time.time()})
    return result, CODES.get(error['code'], 1) if error else 0
