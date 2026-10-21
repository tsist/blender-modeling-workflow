"""Standalone modeling discovery, protected jobs and local Blender handoff."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from protocol import (CODES, DEFAULT_BLENDER, DEFAULT_JOBS, SCHEMA, VERSION,
                      Failure, envelope, read_json)
import runner


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise Failure('INVALID_REQUEST', message)


def parser():
    value = Parser(description='Standalone Blender Modeling Workflow CLI; Windows execution')
    value.add_argument('--version', action='version', version=VERSION)
    value.add_argument('--blender', type=Path, default=DEFAULT_BLENDER)
    value.add_argument('--jobs-dir', type=Path, default=DEFAULT_JOBS)
    value.add_argument('--timeout', type=float, default=120)
    value.add_argument('--async', dest='background', action='store_true')
    value.add_argument('--compact', action='store_true')
    value.add_argument('--full', action='store_true')
    value.add_argument('--human', action='store_true')
    commands = value.add_subparsers(dest='command', required=True, parser_class=Parser)
    model = commands.add_parser('modeling')
    actions = model.add_subparsers(dest='action', required=True, parser_class=Parser)
    describe = actions.add_parser('describe', help='Read-only schema/provider discovery; no job or credentials')
    describe.add_argument('--operation')
    describe.add_argument('--schema', action='store_true')
    describe.add_argument('--section')
    describe.add_argument('--provider', choices=('tripo-cn', 'meshy', 'tripo-overseas'))
    describe.add_argument('--provider-operation')
    actions.add_parser('run').add_argument('--params', type=Path, required=True)
    commands.add_parser('request').add_argument('file', type=Path)
    jobs = commands.add_parser('job').add_subparsers(dest='action', required=True, parser_class=Parser)
    for name in ('status', 'result', 'cancel'):
        jobs.add_parser(name).add_argument('job_id')
    wait = jobs.add_parser('wait')
    wait.add_argument('job_id')
    wait.add_argument('--wait-seconds', type=float, default=30)
    return value


def main(argv=None):
    args = None
    code = 0
    try:
        args = parser().parse_args(argv)
        if args.compact and args.full:
            raise Failure('INVALID_REQUEST', 'Choose --compact or --full')
        if args.command == 'modeling' and args.action == 'describe':
            if args.background or args.compact:
                raise Failure('INVALID_REQUEST', '--async/--compact does not apply to modeling describe')
            from modeling_interface import describe
            result = describe(args.operation, args.schema, args.section, args.provider, args.provider_operation)
            print(json.dumps(result, ensure_ascii=False, allow_nan=False))
            return 0
        if args.command == 'job':
            if args.background:
                raise Failure('INVALID_REQUEST', '--async does not apply to existing jobs')
            job = runner.job_path(args.jobs_dir, args.job_id)
            if args.action == 'result':
                result = read_json(job / 'result.json')
                code = CODES.get((result.get('error') or {}).get('code'), 0 if result.get('ok') else CODES['WORKER_FAILED'])
            elif args.action == 'wait':
                result = runner.wait_for_job(job, args.wait_seconds)
                code = CODES.get((result.get('error') or {}).get('code'), 0)
            else:
                result = getattr(runner, args.action)(job)
        else:
            request = (read_json(args.file) if args.command == 'request' else
                       {'schema_version': SCHEMA, 'command': 'modeling.run', 'params': read_json(args.params)})
            job = runner.prepare(request, args.blender, args.jobs_dir, args.timeout)
            if args.background:
                result = runner.submit(job)
            else:
                result, code = runner.execute(job)
    except Failure as exc:
        result = envelope(error={'code': exc.code, 'message': str(exc)})
        code = CODES.get(exc.code, CODES['WORKER_FAILED'])
    except OSError as exc:
        result = envelope(error={'code': 'IO_ERROR', 'message': str(exc)})
        code = CODES['IO_ERROR']
    except ValueError as exc:
        result = envelope(error={'code': 'INVALID_REQUEST', 'message': str(exc)})
        code = CODES['INVALID_REQUEST']
    if args and args.compact:
        from modeling_interface import compact
        result = compact(result, 'modeling.run')
    if args and args.human:
        print(f"{'OK' if result['ok'] else 'ERROR'} {result.get('command') or ''} job={result.get('job_id') or '-'}")
        print(json.dumps(result['data'] if result['ok'] else result['error'], ensure_ascii=False, indent=2))
    else:
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return code


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf8')
    raise SystemExit(main())
