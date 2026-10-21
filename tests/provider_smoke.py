"""Offline smoke checks: simulated transport, temporary inputs, no Blender/cloud.

Run with an existing Python 3.11+ runtime: python -B <this file>.
Prints a compact summary and exits nonzero on any failure. No report is written to the checkout.
"""
import hashlib
import io
import json
from pathlib import Path
import struct
import sys
import tempfile
import zlib

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from modeling_workflow_addon import contract as cn
from modeling_workflow_addon.providers import overseas_contract as c
from modeling_workflow_addon.providers.overseas_budget import OverseasAuthorization, OverseasLedger
from modeling_workflow_addon.providers import tripo_overseas as adapter
from modeling_workflow_addon.providers.common import ProviderError


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def png():
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data) & 0xffffffff)
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 6, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(b'\x00\x80\x80\x80\xff')) + chunk(b'IEND', b''))


def glb():
    data = json.dumps({'asset': {'version': '2.0', 'generator': 'offline-smoke-no-model'}}).encode()
    data += b' ' * (-len(data) % 4)
    return struct.pack('<4sII', b'glTF', 2, len(data) + 20) + struct.pack('<I4s', len(data), b'JSON') + data


class Response(io.BytesIO):
    status = 200
    def getheader(self, name):
        return str(len(self.getvalue())) if name == 'Content-Length' else None


class Transport:
    """No credential resolution, sockets, HTTP, uploads or billing occur."""
    def __init__(self, *, fail_submit=False, fail_upload=False, artifact=None, mismatch=False):
        self.calls = []; self.uploads = []; self.fail_submit = fail_submit
        self.fail_upload = fail_upload; self.content = glb() if artifact is None else artifact
        self.mismatch = mismatch

    def api(self, route, reference, payload=None, content_type=None):
        self.calls.append((route, payload))
        assert reference == {'kind': 'environment', 'name': 'UNREAD_OFFLINE_OVERSEAS_KEY'}
        if route == '/account/balance':
            assert payload is None
            return {'balance': 100.25, 'frozen': 0.29}
        if route in adapter.ROUTES.values():
            if self.fail_submit:
                raise ProviderError('Simulated ambiguous submission')
            return {'task_id': 'offline-task-1'}
        if route.startswith('/tasks/'):
            return {'task_id': 'wrong-task' if self.mismatch else route.split('/')[-1],
                    'status': 'success', 'progress': 100, 'credits_consumed': 20.25,
                    'error': 'PRIVATE_FAKE_REMOTE_TEXT',
                    'output': {'model_url': 'https://assets.example.invalid/model.glb?signature=PRIVATE_FAKE_SIGNATURE'}}
        raise AssertionError('Unexpected simulated API route')

    def upload_png(self, reference, raw, *, checkpoint=lambda: None):
        checkpoint(); self.uploads.append(raw)
        if self.fail_upload:
            raise ProviderError('Simulated unresolved upload')
        assert raw == png()
        return {'file_token': 'offline-token-' + str(len(self.uploads))}

    def artifact(self, url):
        assert url.startswith('https://assets.example.invalid/')
        return Response(self.content), io.BytesIO()


def expect_reject(fn):
    try:
        fn()
    except (ProviderError, cn.ContractError):
        return
    raise AssertionError('Expected a fixed contract/provider rejection')


def fixture(base, op='h31_image', cap=40, reservation=30):
    base.mkdir(parents=True, exist_ok=True)
    rows = []
    for view in (('front',) if op == 'h31_image' else ('front', 'back')):
        path = base / (view + '.png'); path.write_bytes(png())
        rows.append({'view': view, 'file': str(path), 'expected_sha256': digest(path.read_bytes())})
    request = {'operation_id': 'offline-op-1', 'operation': op, 'inputs': rows}
    ledger = base / 'overseas-ledger.json'
    approval = {'schema_version': '1.0', 'approved': True, 'approval_id': 'offline-approval',
                'provider': c.PROVIDER, 'region': c.REGION, 'base_url': c.BASE,
                'ledger_file': str(ledger), 'total_cap_credits': cap, 'max_attempts': 1,
                'allows_upload': True, 'operations': [{'operation_id': request['operation_id'],
                  'operation': op, 'request_sha256': c.request_sha256(request), 'max_credits': reservation}]}
    authorization = base / 'authorization.json'; authorization.write_text(json.dumps(approval), encoding='utf8')
    return {'schema_version': '1.1', 'provider': c.PROVIDER, 'operation': 'submit',
            'credential_ref': {'provider': c.PROVIDER, 'region': c.REGION,
              'reference': {'kind': 'environment', 'name': 'UNREAD_OFFLINE_OVERSEAS_KEY'}},
            'request': request, 'ledger_file': str(ledger),
            'authorization_descriptor': {'file': str(authorization), 'expected_sha256': digest(authorization.read_bytes())}}


def query_manifest(manifest, action='query'):
    result = {key: manifest[key] for key in ('schema_version', 'provider', 'credential_ref')}
    result.update(operation=action, task_id='offline-task-1')
    if action == 'download':
        result['max_download_bytes'] = 1024
    return result


def main():
    results = []
    def check(name, fn):
        try:
            fn(); results.append({'check': name, 'status': 'pass'})
        except Exception as exc:
            # Do not persist transport bodies, credentials, signed URLs or tracebacks.
            results.append({'check': name, 'status': 'fail', 'exception': type(exc).__name__})
    def equal(actual, expected):
        assert actual == expected

    with tempfile.TemporaryDirectory(prefix='overseas-adapter-basic-') as temp:
        root = Path(temp).resolve()
        single = fixture(root / 'single'); multi = fixture(root / 'multi', 'h31_multiview')
        def wire(manifest, name):
            transport = Transport(); result = adapter.run_manifest(manifest, root / (name + '-job'), transport)
            posted = [(route, body) for route, body in transport.calls if body is not None]
            equal(len(posted), 1)
            body = posted[0][1]
            expected = {'model': c.MODEL, **c.WIRE_OPTIONS}
            if manifest['request']['operation'] == 'h31_image':
                expected['input'] = 'offline-token-1'
            else:
                expected['inputs'] = [{'front': 'offline-token-1'}, {'back': 'offline-token-2'}]
            equal(body, expected)
            equal(posted[0][0], adapter.ROUTES[manifest['request']['operation']])
            equal(result['task_identity'], {'provider': c.PROVIDER, 'region': c.REGION,
                                           'base_url': c.BASE, 'task_id': 'offline-task-1'})
            equal(len(transport.uploads), len(manifest['request']['inputs']))
        check('single-view-fixed-wire-and-isolated-task', lambda: wire(single, 'single'))
        check('multi-view-front-back-fixed-wire', lambda: wire(multi, 'multi'))
        check('request-extra-wire-rejected', lambda: expect_reject(lambda: c.normalize_request({**single['request'], 'texture': True})))
        check('bare-cn-credential-reference-rejected', lambda: expect_reject(lambda: c.normalize_manifest({**single, 'credential_ref': single['credential_ref']['reference']})))
        check('unsupported-resume-rejected', lambda: expect_reject(lambda: c.normalize_manifest({**query_manifest(single), 'operation': 'resume_download'})))
        check('download-requires-byte-limit', lambda: expect_reject(lambda: c.normalize_manifest(query_manifest(single) | {'operation': 'download'})))
        check('decimal-credit-exactness', lambda: equal(c.money_cents(0.29) + c.money_cents(0.01), 30))
        def invalid_money():
            for value in (True, float('nan'), float('inf'), -1, 0.001, '1.00'):
                expect_reject(lambda value=value: c.money(value))
        check('invalid-credit-values-rejected', invalid_money)
        check('request-sha-domain-isolated-from-cn', lambda: equal(c.request_sha256(single['request']) != cn.request_sha256(single['request']), True))
        for label, document in (('cn', {'schema_version': '1.0', 'provider': 'tripo-cn'}),
                                ('legacy-research', {'schema_version': '1.0', 'provider': 'tripo-overseas', 'research_only': True, 'reservation': 'synthetic-old-reservation'})):
            path = root / (label + '.json'); path.write_text(json.dumps(document))
            check('nonformal-ledger-rejected-' + label, lambda path=path: expect_reject(lambda: OverseasLedger(path)))
        def balance():
            manifest = {key: single[key] for key in ('schema_version', 'provider', 'credential_ref')}
            result = adapter.run_manifest({**manifest, 'operation': 'account_balance'}, root / 'balance', Transport())
            equal((result['balance'], result['frozen']), (100.25, 0.29))
            equal(result['accounting'], 'read_only_not_a_budget_authorization')
        check('account-balance-read-only-decimals', balance)
        def redaction():
            value = adapter.run_manifest(query_manifest(single), root / 'query', Transport())
            text = json.dumps(value)
            assert 'PRIVATE_FAKE_' not in text and 'https://' not in text.replace(c.BASE, '')
            equal(value['accounting']['ledger_modified'], False)
            equal(value['available_output_formats'], ['glb'])
        check('query-output-redacts-url-and-remote-text', redaction)
        check('query-task-mismatch-rejected', lambda: expect_reject(lambda: adapter.run_manifest(query_manifest(single), root / 'mismatch', Transport(mismatch=True))))
        def ambiguity(upload=False):
            manifest = fixture(root / ('upload-fail' if upload else 'post-fail'))
            transport = Transport(fail_submit=not upload, fail_upload=upload)
            job = root / ('upload-fail-job' if upload else 'post-fail-job')
            expect_reject(lambda: adapter.run_manifest(manifest, job, transport))
            before_calls = len([body for _, body in transport.calls if body is not None])
            before_uploads = len(transport.uploads)
            expect_reject(lambda: adapter.run_manifest(manifest, job, transport))
            equal(len([body for _, body in transport.calls if body is not None]), before_calls)
            equal(len(transport.uploads), before_uploads)
            snapshot = OverseasLedger(manifest['ledger_file']).snapshot()
            equal(snapshot['ledger']['attempts'][0]['state'], 'upload_unresolved' if upload else 'unknown')
            equal(snapshot['worst_or_accounted_credits'], 30)
        check('ambiguous-post-no-resubmit-reservation-retained', ambiguity)
        check('unresolved-upload-no-repeat-reservation-retained', lambda: ambiguity(True))
        def accounting():
            ledger = OverseasLedger(single['ledger_file'])
            ledger.observe('offline-task-1', 'succeeded')
            equal(ledger.snapshot()['worst_or_accounted_credits'], 30)
            ledger.observe('offline-task-1', 'succeeded', 30.01, 'a' * 64)
            equal(ledger.snapshot()['ledger']['halted'], True)
            ledger.observe('offline-task-1', 'succeeded', 30.01, 'b' * 64)
            equal(ledger.snapshot()['ledger']['attempts'][0]['receipt_sha256'], 'a' * 64)
            expect_reject(lambda: ledger.observe('offline-task-1', 'running'))
            expect_reject(lambda: ledger.observe('other-task', 'succeeded', 0, 'c' * 64))
        check('terminal-accounting-cap-halt-and-no-unknown-bind', accounting)
        def download():
            result = adapter.run_manifest(query_manifest(single, 'download'), root / 'download', Transport())
            equal(result['sha256'], digest(glb()))
            equal(Path(result['path']).read_bytes(), glb())
            equal(result['bytes'], len(glb()))
            assert not Path(result['path'] + '.partial').exists()
        check('download-glb-container-bytes-sha-and-promotion', download)
        def malformed():
            job = root / 'bad-format'
            expect_reject(lambda: adapter.run_manifest(query_manifest(single, 'download'), job, Transport(artifact=b'not-a-glb-container')))
            assert (job / 'original-model.glb.partial').exists()
            assert not (job / 'original-model.glb').exists()
        check('download-invalid-format-keeps-partial', malformed)
        check('download-byte-limit-rejected', lambda: expect_reject(lambda: adapter.run_manifest(query_manifest(single, 'download') | {'max_download_bytes': 12}, root / 'too-large', Transport())))
        def changed_input():
            manifest = fixture(root / 'changed-input')
            Path(manifest['request']['inputs'][0]['file']).write_bytes(b'changed')
            transport = Transport()
            expect_reject(lambda: adapter.run_manifest(manifest, root / 'changed-job', transport))
            equal(transport.calls, [])
        check('changed-png-sha-rejected-before-api', changed_input)
        def discovery():
            schemas = c.schemas()
            equal(schemas['overseas_submit']['properties']['provider']['const'], c.PROVIDER)
            equal(schemas['overseas_download']['properties']['max_download_bytes']['maximum'], 2 * 1024 ** 3)
        check('overseas-schema-fixed-identity-and-bounds', discovery)

    report = {'ok': all(row['status'] == 'pass' for row in results),
              'passed': sum(row['status'] == 'pass' for row in results), 'total': len(results),
              'scope': 'offline simulated basic checks; no modeling or service parity claim',
              'network_calls': 0, 'blender_calls': 0, 'real_credential_reads': 0, 'real_financial_ledger_reads': 0,
              'rerun': 'python -B "' + str(Path(__file__).resolve()) + '"', 'checks': results}
    print(json.dumps({key: report[key] for key in ('ok', 'passed', 'total', 'network_calls', 'blender_calls')}))
    for row in results:
        if row['status'] != 'pass':
            print(json.dumps(row))
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
