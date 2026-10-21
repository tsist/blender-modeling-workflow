"""One bounded pass of known task GETs, inside a public CLI job."""
from pathlib import Path

from .common import ProviderError, atomic_json


def summary(progress):
    return {key: progress[key] for key in ('status', 'requested_count', 'attempted_count',
            'completed_count', 'remaining_task_ids', 'active_task_id', 'stop_code')}


def run(provider, task_ids, job_directory, checkpoint):
    # normalize_manifest validates the complete list before this entry. The
    # journal is evidence, never authorization or an automatic resume plan.
    target = Path(job_directory) / 'query-batch.json'
    if target.exists():
        raise ProviderError('Query batch evidence already exists; use a new public job')
    progress = {'schema_version': '1.0', 'operation': 'query_batch', 'status': 'running',
                'requested_task_ids': list(task_ids), 'requested_count': len(task_ids),
                'attempted_count': 0, 'completed_count': 0, 'completed': [],
                'remaining_task_ids': list(task_ids), 'active_task_id': None, 'stop_code': None,
                'policy': 'One sequential GET per task; no polling, retries or paid submissions'}
    atomic_json(target, progress)
    try:
        for index, task_id in enumerate(task_ids):
            checkpoint()
            progress.update(active_task_id=task_id, attempted_count=index + 1)
            atomic_json(target, progress)
            result = provider.query(task_id)
            # A remotely failed/running task is still a successful observation.
            # Preserve the safe snapshot before honoring cancellation or moving on.
            progress['completed'].append(result)
            progress.update(completed_count=index + 1, remaining_task_ids=task_ids[index + 1:],
                            active_task_id=None)
            atomic_json(target, progress)
            checkpoint()
        progress['status'] = 'passed'
        atomic_json(target, progress)
        return {'batch_summary': summary(progress), 'query_batch_file': str(target)}
    except BaseException as exc:
        code = getattr(exc, 'code', None)
        if code not in ('RATE_LIMITED', 'CANCELLED', 'TIMEOUT'):
            code = 'CANCELLED' if isinstance(exc, KeyboardInterrupt) else 'QUERY_FAILED'
        progress.update(status='stopped', stop_code=code)
        # Do not persist arbitrary exception text or a provider response.
        atomic_json(target, progress)
        raise
