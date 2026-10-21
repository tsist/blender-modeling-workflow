"""Explicit bounded batches inside one public job, without POST retries.

The journal is evidence only. The original approval-bound financial ledger
decides whether an operation was accepted; a new job may only query that task.
"""
from contextlib import ExitStack
from pathlib import Path
import os
import re
import time

from ..contract import canonical_path, identity, request_sha256, sha
from .budget import BudgetLedger, TrustedAuthorization, TERMINAL
from .common import PathLocks, atomic_json
from protocol import Failure


SUMMARY_FIELDS = ('operation', 'status', 'requested_count', 'completed_count',
                  'remaining_ids', 'active_id', 'active_task_id', 'query_count',
                  'stop_code', 'stage', 'active_directory')
ACCEPTED = {'confirmed', 'queued', 'running', 'succeeded'}


def summary(progress):
    return {key: progress[key] for key in SUMMARY_FIELDS}


def _begin(manifest, job_directory, ids):
    target = Path(job_directory).resolve() / 'modeling-batch.json'
    # A journal cannot be reused or replaced, even for another ledger/job.
    with PathLocks([target]):
        if os.path.lexists(target):
            raise Failure('CONFLICT', 'Batch evidence already exists; use a new public job')
        progress = {'schema_version': '1.0', 'operation': manifest['operation'],
                    'status': 'running', 'requested_count': len(ids),
                    'completed_count': 0, 'completed': [], 'remaining_ids': list(ids),
                    'active_id': None, 'active_task_id': None, 'query_count': 0,
                    'stop_code': None, 'stage': 'preflight', 'active_directory': None,
                    'current_item': None}
        if manifest['operation'] == 'submit_batch':
            progress.update(query_limit=manifest['query_limit'],
                            poll_interval_seconds=manifest['poll_interval_seconds'],
                            query_budget_scope='Task GET attempts in this public job; balance GETs excluded',
                            policy='Sequential approved submissions; accepted tasks reused; no POST retry')
        else:
            progress.update(max_total_bytes=manifest['max_total_bytes'], completed_bytes=0,
                            policy='Sequential known-task downloads; first error stops; sources read-only; no retry')
        atomic_json(target, progress)
    return target, progress


def _save(target, progress, **changes):
    progress.update(changes)
    atomic_json(target, progress)


def _stop(target, progress, exc):
    code = getattr(exc, 'code', None)
    if isinstance(exc, KeyboardInterrupt):
        code = 'CANCELLED'
    if not isinstance(code, str) or not re.fullmatch(r'[A-Z][A-Z0-9_]{0,63}', code):
        code = 'BATCH_FAILED'
    _save(target, progress, status='stopped', stop_code=code)


def _settled(row):
    # Zero credits is an explicit, valid settlement; never use truthiness.
    return (row['state'] in TERMINAL and 'accounted_credits' in row
            and 'receipt_sha256' in row)


def _row(snapshot, request, policy, task_id=None):
    rows = [row for row in snapshot['ledger']['attempts']
            if row['operation_id'] == request['operation_id']]
    if len(rows) > 1:
        raise Failure('CONFLICT', 'Batch operation identity is ambiguous')
    if not rows:
        if task_id is not None:
            raise Failure('CONFLICT', 'Accepted batch operation disappeared')
        return None
    row = rows[0]
    if (row['operation'] != request['operation']
            or row['request_sha256'] != request_sha256(request)
            or row['reserved_credits'] != policy['max_credits']
            or (task_id is not None and row['task_id'] != task_id)):
        raise Failure('CONFLICT', 'Batch operation conflicts with its original approval or task')
    return row


def _preflight_submit(provider, authorization, manifest, checkpoint):
    ledger = provider.ledger
    snapshot = ledger.snapshot()
    data = snapshot['ledger']
    if data['halted']:
        raise Failure('RESOURCE_LIMIT', 'Original financial ledger is halted')
    requests = manifest['requests']
    by_id = {request['operation_id']: request for request in requests}
    legacy_ids = {row['operation_id'] for row in data.get('legacy_history', [])}
    policies = {}
    new_count = new_credits = 0
    for request in requests:
        checkpoint()
        op_id = request['operation_id']
        if op_id in legacy_ids:
            raise Failure('CONFLICT', 'Legacy operation identities cannot be replayed by a batch')
        policy = authorization.operation(request, request_sha256(request))
        policies[op_id] = policy
        # The provider exposes this as local-only: no balance GET or uploads.
        provider.validate_submission(request, authorization)
        row = _row(snapshot, request, policy)
        if row is None:
            new_count += 1
            new_credits += policy['max_credits']
        elif row['state'] not in ACCEPTED or row['task_id'] is None:
            raise Failure('CONFLICT', 'Existing batch attempt requires recovery; never resubmit')
    for row in data['attempts']:
        if not _settled(row) and (row['operation_id'] not in by_id
                                  or row['state'] not in ACCEPTED or row['task_id'] is None):
            raise Failure('CONFLICT', 'Unsettled original reservation requires explicit recovery')
    worst = snapshot['worst_or_accounted_credits'] + new_credits
    if worst > data['approval']['total_cap_credits']:
        raise Failure('RESOURCE_LIMIT', 'Whole batch worst-case credits exceed the original approval cap')
    if snapshot['attempts_used'] + new_count > data['approval']['max_attempts']:
        raise Failure('RESOURCE_LIMIT', 'Whole batch attempt count exceeds the original approval')
    checkpoint()
    return policies, worst


def _wait(interval, checkpoint):
    deadline = time.monotonic() + interval
    while True:
        checkpoint()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(0.25, remaining))


def _budget(progress):
    if progress['query_count'] >= progress['query_limit']:
        raise Failure('RESOURCE_LIMIT', 'Batch task GET budget exhausted; use an explicit new job')


def _completed_submit(row, request, reused):
    return {'operation_id': request['operation_id'], 'operation': request['operation'],
            'request_sha256': row['request_sha256'], 'attempt_id': row['id'],
            'task_id': row['task_id'], 'state': row['state'],
            'reserved_credits': row['reserved_credits'],
            'accounted_credits': row['accounted_credits'],
            'receipt_sha256': row['receipt_sha256'], 'reused_accepted_task': reused}


def run_submit(manifest, job_directory, transport, checkpoint):
    from .tripo_cn import TripoCNProvider
    # Internal batches also preserve the explicit new-provider entry boundary.
    # Public normalization remains responsible for all other manifest fields.
    if any(request['operation'] == 'h31_parts_multiview' for request in manifest['requests']):
        if manifest.get('schema_version') != '1.1' or manifest.get('provider') != 'tripo-cn':
            raise Failure('VALIDATION_FAILED', 'Native parts requires explicit manifest 1.1 tripo-cn')
    # Acquire before authorization reads, ledger creation and replay lookup.
    with PathLocks([manifest['ledger_file']]):
        target, progress = _begin(manifest, job_directory,
                                  [request['operation_id'] for request in manifest['requests']])
        try:
            checkpoint()
            authorization = TrustedAuthorization.from_descriptor(manifest['authorization_descriptor'])
            if canonical_path(authorization.document['ledger_file']) != canonical_path(manifest['ledger_file']):
                raise Failure('CONFLICT', 'Batch approval is bound to a different ledger')
            ledger = BudgetLedger(manifest['ledger_file'], authorization)
            provider = TripoCNProvider(manifest['credential_ref'], ledger, transport)
            policies, worst = _preflight_submit(provider, authorization, manifest, checkpoint)
            _save(target, progress, stage='ready', worst_batch_credits=worst,
                  approval_sha256=authorization.approval_sha256)
            for request in manifest['requests']:
                checkpoint()
                op_id = request['operation_id']
                policy = policies[op_id]
                snapshot = ledger.snapshot()
                if snapshot['ledger']['halted']:
                    raise Failure('RESOURCE_LIMIT', 'Original financial ledger is halted')
                row = _row(snapshot, request, policy)
                reused = row is not None
                _save(target, progress, active_id=op_id,
                      active_task_id=row['task_id'] if row else None,
                      current_item={'operation_id': op_id, 'operation': request['operation'],
                                    'request_sha256': request_sha256(request)}, stage='item_preflight')
                if row is None:
                    # Do not pay for a task that this job cannot even query once.
                    _budget(progress)
                    if any(not _settled(other) for other in snapshot['ledger']['attempts']):
                        raise Failure('CONFLICT', 'Outstanding original reservation must be settled before a new submission')
                    checkpoint()
                    _save(target, progress, stage='submitting')
                    receipt = provider.submit(request, authorization, checkpoint=checkpoint)
                    task_id = identity(receipt.get('task_id'))
                    # Publish the accepted identity before any further lookup.
                    _save(target, progress, stage='accepted', active_task_id=task_id)
                    row = _row(ledger.snapshot(), request, policy, task_id)
                    if row['state'] not in ACCEPTED:
                        raise Failure('CONFLICT', 'Submission did not retain an accepted original task')
                    _save(target, progress, stage='accepted', active_task_id=row['task_id'],
                          active_attempt_id=row['id'])
                elif row['state'] not in ACCEPTED or row['task_id'] is None:
                    raise Failure('CONFLICT', 'Existing batch attempt requires recovery; never resubmit')
                task_id = row['task_id']
                while not (row['state'] == 'succeeded' and _settled(row)):
                    checkpoint()
                    _budget(progress)
                    _save(target, progress, stage='querying',
                          query_count=progress['query_count'] + 1)
                    # Count before sending. Any HTTP/query error stops the job.
                    provider.query(task_id)
                    snapshot = ledger.snapshot()
                    row = _row(snapshot, request, policy, task_id)
                    observation = {'task_id': task_id, 'state': row['state'],
                                   'settled': _settled(row)}
                    if _settled(row):
                        observation.update(accounted_credits=row['accounted_credits'],
                                           receipt_sha256=row['receipt_sha256'])
                    _save(target, progress, stage='observed', last_observation=observation)
                    if snapshot['ledger']['halted']:
                        raise Failure('RESOURCE_LIMIT', 'Original financial ledger is halted after observation')
                    if row['state'] in ('failed', 'cancelled'):
                        raise Failure('VALIDATION_FAILED', 'Remote batch task reached an unsuccessful terminal state')
                    if row['state'] not in ACCEPTED:
                        raise Failure('CONFLICT', 'Remote batch observation has an unsupported ledger state')
                    if row['state'] == 'succeeded' and _settled(row):
                        break
                    _budget(progress)
                    _save(target, progress, stage='awaiting_settlement' if row['state'] == 'succeeded' else 'poll_wait')
                    _wait(manifest['poll_interval_seconds'], checkpoint)
                progress['completed'].append(_completed_submit(row, request, reused))
                _save(target, progress, completed_count=len(progress['completed']),
                      remaining_ids=progress['remaining_ids'][1:], active_id=None,
                      active_task_id=None, current_item=None, stage='item_complete')
                checkpoint()
            _save(target, progress, status='passed', stage='complete')
            return {'batch_summary': summary(progress), 'batch_report_file': str(target)}
        except BaseException as exc:
            _stop(target, progress, exc)
            raise


def _completed_download(result, item, directory, index):
    expected_path = directory / 'original-model.glb'
    if (result.get('state') != 'complete' or result.get('task_id') != item['task_id']
            or canonical_path(result.get('path', '')) != canonical_path(str(expected_path))
            or type(result.get('bytes')) is not int or not 12 <= result['bytes'] <= item['max_bytes']):
        raise Failure('CONFLICT', 'Download returned inconsistent batch output evidence')
    checksum = sha(result.get('sha256'))
    out = {'index': index, 'operation': item['operation'], 'task_id': item['task_id'],
           'state': 'complete', 'provider': 'tripo-cn', 'path': str(expected_path),
           'bytes': result['bytes'], 'sha256': checksum,
           'checkpoint_file': str(expected_path) + '.partial.json'}
    for key in ('expected_bytes', 'resumed_bytes'):
        value = result.get(key)
        if value is None and key == 'expected_bytes':
            out[key] = None
        elif type(value) is int and 0 <= value <= item['max_bytes']:
            out[key] = value
        else:
            raise Failure('CONFLICT', 'Download returned inconsistent batch byte evidence')
    if item['operation'] == 'resume_download':
        out['resumed_from'] = item['partial_descriptor']
    return out


def run_download(provider, manifest, job_directory, checkpoint):
    from .download import read_recovery
    with PathLocks([manifest['ledger_file']]):
        target, progress = _begin(manifest, job_directory,
                                  [item['task_id'] for item in manifest['items']])
        try:
            with ExitStack() as sources:
                # Validate every original recovery source before any network.
                for item in manifest['items']:
                    checkpoint()
                    if item['operation'] == 'resume_download':
                        read_recovery(item['recovery_descriptor'], item['partial_descriptor'],
                                      item['task_id'], item['max_bytes'], sources, checkpoint)
                _save(target, progress, stage='ready')
                for index, item in enumerate(manifest['items']):
                    checkpoint()
                    directory = Path(job_directory).resolve() / 'batch-items' / f'{index:03d}'
                    output = directory / 'original-model.glb'
                    _save(target, progress, stage='downloading', active_id=item['task_id'],
                          active_task_id=item['task_id'], active_directory=str(directory),
                          current_item=dict(item))
                    result = provider.download(item['task_id'], output, item['max_bytes'],
                                               recovery=item.get('recovery_descriptor'),
                                               partial_descriptor=item.get('partial_descriptor'),
                                               checkpoint=checkpoint)
                    completed = _completed_download(result, item, directory, index)
                    checkpoint()
                    progress['completed'].append(completed)
                    _save(target, progress, stage='item_complete',
                          completed_count=len(progress['completed']),
                          completed_bytes=progress['completed_bytes'] + completed['bytes'],
                          remaining_ids=progress['remaining_ids'][1:], active_id=None,
                          active_task_id=None, active_directory=None, current_item=None)
                    checkpoint()
                _save(target, progress, status='passed', stage='complete')
                return {'batch_summary': summary(progress), 'batch_report_file': str(target)}
        except BaseException as exc:
            directory = progress['active_directory']
            item = progress['current_item']
            if directory is not None and item is not None:
                partial = Path(directory) / 'original-model.glb.partial'
                state_file = Path(str(partial) + '.json')
                if partial.is_file() or state_file.is_file():
                    progress['failure_recovery'] = {'task_id': item['task_id'],
                        'max_bytes': item['max_bytes'], 'partial_file': str(partial),
                        'checkpoint_file': str(state_file), 'partial_exists': partial.is_file(),
                        'checkpoint_exists': state_file.is_file(), 'validated': False}
            _stop(target, progress, exc)
            raise
