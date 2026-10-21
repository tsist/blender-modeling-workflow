"""Modeling-only Blender worker; JSON input and owned process-tree gate."""
from pathlib import Path
import sys
import time
import traceback

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from protocol import Failure, atomic_json, read_json, validate


def main():
    job = Path(sys.argv[sys.argv.index('--') + 1]).resolve()
    deadline = time.monotonic() + 10
    while not (job / 'worker.gate').exists():
        if time.monotonic() > deadline:
            raise RuntimeError('Supervisor did not acquire process-tree ownership')
        time.sleep(0.02)
    try:
        request = validate(read_json(job / 'request.json'))
        if request['params']['manifest']['operation'] != 'import_local':
            raise Failure('INVALID_REQUEST', 'Cloud operations require the non-Blender host')
        import modeling_workflow
        data = modeling_workflow.run(request['params'], job)
        atomic_json(job / 'worker-result.json', {'ok': True, 'data': data, 'error': None})
    except Exception as exc:
        atomic_json(job / 'worker-result.json', {'ok': False, 'data': None,
                    'error': {'type': type(exc).__name__, 'message': str(exc),
                              'code': exc.code if isinstance(exc, Failure) else 'WORKER_FAILED'}})
        traceback.print_exc()
        raise


if __name__ == '__main__':
    main()
