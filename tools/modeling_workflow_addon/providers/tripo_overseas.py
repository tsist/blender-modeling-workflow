"""Explicit overseas Tripo v3: fixed basic generation and isolated accounting.

The inherited upload/storage primitives are region-neutral; API and finance are
not delegated to China. There are no retries, cross-region fallback or migration.
"""
from contextlib import ExitStack
import hashlib
import http.client
import json
import math
from pathlib import Path
import ssl
import time
import urllib.parse

from ..contract import ContractError
from .common import ProviderError, RateLimitError, ArtifactAccessError, FileGuard, atomic_json
from .overseas_contract import (PROVIDER, REGION, BASE, MODEL, OPERATIONS, WIRE_OPTIONS,
                               normalize_manifest, request_sha256, task_id, money, money_cents)
from .overseas_budget import OverseasAuthorization, OverseasLedger, TERMINAL
from .tripo_cn import HTTPTransport, _credential, _png, _validate_glb
from .transport_diagnostics import SafeProviderError, transport_failure

ROUTES = {'h31_image': '/generation/image-to-model', 'h31_multiview': '/generation/multiview-to-model'}
STATES = {'queued': 'queued', 'running': 'running', 'success': 'succeeded',
          'failed': 'failed', 'cancelled': 'cancelled'}


def task_identity(value):
    return {'provider': PROVIDER, 'region': REGION, 'base_url': BASE, 'task_id': task_id(value)}


class OverseasHTTPTransport(HTTPTransport):
    """Direct fixed overseas host; ordinary TLS, no environment proxy or redirect.

    The 45 second socket timeout is an idle timeout, not a total job deadline.
    API business codes are recorded as numbers without CN semantic labels.
    """
    def api(self, route, reference, payload=None, content_type=None):
        import re
        fixed = ('/account/balance', '/files/presign', *ROUTES.values())
        if route not in fixed and not re.fullmatch(r'/tasks/[A-Za-z0-9_-]{1,128}', route):
            raise ProviderError('Unsupported overseas API route')
        key = None; connection = None; headers = {}; status = None
        try:
            key = _credential(reference)
            headers = {'Authorization': 'Bearer ' + key, 'Accept': 'application/json'}
            body = None
            if payload is not None:
                if not isinstance(payload, dict):
                    raise ProviderError('Overseas API requires a bounded JSON object')
                body = json.dumps(payload, allow_nan=False).encode('utf8')
                if len(body) > 64 * 1024:
                    raise ProviderError('Overseas API payload exceeds size bound')
                headers['Content-Type'] = 'application/json'
            connection = http.client.HTTPSConnection('openapi.tripo3d.ai', 443, timeout=45,
                                                      context=ssl.create_default_context())
            connection.request('GET' if body is None else 'POST', '/v3' + route,
                               body=body, headers=headers)
            response = connection.getresponse(); status = response.status
            raw = response.read(4 * 1024 * 1024 + 1)
            if len(raw) > 4 * 1024 * 1024:
                raise SafeProviderError(http_status=status, category='invalid_response')
            try:
                document = json.loads(raw)
            except Exception:
                document = None
            if status == 429:
                delay = response.getheader('Retry-After')
                delay = int(delay) if isinstance(delay, str) and delay.isascii() and delay.isdecimal() and len(delay) <= 5 else None
                if delay is not None and delay > 86400:delay = None
                # RateLimitError's named CN codes are intentionally not reused.
                raise RateLimitError(http_method='GET' if body is None else 'POST', retry_after_seconds=delay)
            if (status != 200 or not isinstance(document, dict)
                    or type(document.get('code')) is not int or document['code'] != 0
                    or not isinstance(document.get('data'), dict)):
                raise SafeProviderError(http_status=status, category='invalid_response')
            return document['data']
        except (ProviderError, KeyboardInterrupt, SystemExit):
            raise
        except (TimeoutError, OSError) as exc:
            raise transport_failure(exc, http_status=status) from None
        except Exception:
            raise SafeProviderError(http_status=status, category='invalid_response') from None
        finally:
            key = None; headers.clear()
            if connection is not None:
                try:connection.close()
                except Exception:pass


def _query(manifest, transport, checkpoint):
    remote = task_id(manifest['task_id'])
    # Validate an explicitly selected existing ledger before making any GET.
    ledger = OverseasLedger(manifest['ledger_file']) if 'ledger_file' in manifest else None
    if ledger is not None and not any(r['task_id'] == remote for r in ledger.snapshot()['ledger']['attempts']):
        raise ProviderError('Selected overseas ledger does not contain this task')
    checkpoint();data = transport.api('/tasks/' + remote, manifest['credential_ref']['reference'])
    if not isinstance(data, dict) or data.get('task_id') != remote:
        raise ProviderError('Overseas task identity mismatch')
    native = data.get('status')
    if native == 'expired':raise ArtifactAccessError(task_expired=True)
    if native not in STATES:raise ProviderError('Unsupported overseas task status')
    safe = {'provider': PROVIDER, 'task_identity': task_identity(remote), 'task_id': remote,
            'status': native, 'state': STATES[native], 'observed_at': time.time()}
    progress = data.get('progress')
    if progress is not None:
        if type(progress) not in (int, float) or not math.isfinite(progress) or not 0 <= progress <= 100:
            raise ProviderError('Invalid overseas task progress')
        safe['progress'] = progress
    credits = data.get('credits_consumed')
    if credits is not None:
        try:safe['credits_consumed'] = money(credits)
        except ContractError:raise ProviderError('Invalid overseas task credit consumption') from None
    if type(data.get('error_code')) is int:safe['error_code'] = data['error_code']
    output = data.get('output')
    url = output.get('model_url') if isinstance(output, dict) else None
    safe['model_output_available'] = isinstance(url, str) and bool(url)
    try:is_glb = isinstance(url, str) and urllib.parse.urlsplit(url).path.lower().endswith('.glb')
    except ValueError:raise ProviderError('Invalid overseas output reference') from None
    safe['available_output_formats'] = ['glb'] if is_glb else []
    if ledger is not None:
        receipt = hashlib.sha256(json.dumps(safe, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
        row = ledger.observe(remote, STATES[native], credits_consumed=money(credits) if credits is not None and STATES[native] in TERMINAL else None,
                             receipt_sha256=receipt if credits is not None and STATES[native] in TERMINAL else None)
        safe['accounting'] = {'mode': 'existing_overseas_ledger', 'state': row['state'],
                              'accounted_credits': row.get('accounted_credits'), 'halted': ledger.snapshot()['ledger']['halted']}
    else:safe['accounting'] = {'mode': 'observation_only', 'ledger_modified': False}
    checkpoint()
    return data, safe


def _submit(manifest, job, transport, checkpoint):
    from .image_upload import UploadError
    request = manifest['request'];reference = manifest['credential_ref']['reference']
    with ExitStack() as stack:
        descriptors = [manifest['authorization_descriptor'], *request['inputs']]
        for descriptor in descriptors:
            guard = stack.enter_context(FileGuard(descriptor['file']))
            if guard.sha256(checkpoint) != descriptor['expected_sha256']:
                raise ProviderError('Overseas immutable submission input changed')
        authorization = OverseasAuthorization.from_descriptor(manifest['authorization_descriptor'])
        if authorization.document['ledger_file'] != manifest['ledger_file']:
            raise ProviderError('Overseas approval belongs to another ledger path')
        checksum = request_sha256(request);policy = authorization.operation(request, checksum)
        if not authorization.document['allows_upload'] or money_cents(policy['max_credits']) < 2000:
            raise ProviderError('Overseas generation requires approved uploads and minimum documented reservation')
        raw_inputs = [_png(row) for row in request['inputs']]
        ledger = OverseasLedger(manifest['ledger_file'], authorization)
        checkpoint();balance = transport.api('/account/balance', reference)
        if not isinstance(balance, dict):raise ProviderError('Invalid overseas balance response')
        try:
            available = money_cents(balance.get('balance'))
            money_cents(balance.get('frozen'))
        except ContractError:raise ProviderError('Invalid overseas balance response') from None
        # Official account docs define balance as available credits; frozen is
        # a separate field and must not be subtracted from available twice.
        if available < money_cents(policy['max_credits']):
            raise ProviderError('Overseas balance cannot cover approved reservation; no recharge')
        attempt = ledger.reserve(request, checksum);uploads = []
        try:
            tokens=[]
            for raw in raw_inputs:
                checkpoint();reply = transport.upload_png(reference, raw, checkpoint=checkpoint)
                token = reply.get('file_token') if isinstance(reply, dict) else None
                if not isinstance(token, str) or not 1 <= len(token) <= 4096 or any(ord(c) < 33 or ord(c) > 126 for c in token):
                    raise ProviderError('Invalid overseas upload receipt')
                tokens.append(token)
                if isinstance(reply.get('upload_receipt'), dict):
                    uploads.append({key: reply['upload_receipt'][key] for key in (
                        'stage', 'method', 'total_bytes', 'locally_completed_send_bytes',
                        'http_status', 'elapsed_seconds', 'input_sha256',
                        'expiry_observed', 'automatic_retry') if key in reply['upload_receipt']})
        except BaseException as exc:
            ledger.mark_upload_unresolved(attempt['id'])
            error = exc if isinstance(exc, (UploadError, SafeProviderError, RateLimitError, KeyboardInterrupt, SystemExit)) else ProviderError('Overseas upload unresolved; reservation retained; do not retry')
            error.uploads=uploads;raise error from None
        body = {'model': MODEL, **WIRE_OPTIONS}
        if request['operation']=='h31_image':body['input']=tokens[0]
        else:body['inputs']=[{'front':tokens[0]},{'back':tokens[1]}]
        checkpoint();ledger.mark_submitting(attempt['id'])
        try:
            accepted=transport.api(ROUTES[request['operation']], reference, body)
            remote=task_id(accepted.get('task_id'))
        except BaseException as exc:
            ledger.mark_unknown(attempt['id'])
            if isinstance(exc, RateLimitError):
                raise RateLimitError(http_method='POST', retry_after_seconds=exc.details['retry_after_seconds'], submission_unknown=True) from None
            raise ProviderError('Overseas submission outcome unknown; reservation retained; never resubmit') from None
        receipt={'schema_version':'1.0','task_identity':task_identity(remote),'attempt_id':attempt['id'],
                 'operation_id':request['operation_id'],'request_sha256':checksum,'accepted_at':time.time()}
        receipt_file=job/'modeling-overseas-acceptance.json'
        receipt_sha=None;receipt_persisted=False
        try:
            atomic_json(receipt_file,receipt)
            receipt_sha=hashlib.sha256(receipt_file.read_bytes()).hexdigest()
            receipt_persisted=True
            row=ledger.confirm(attempt['id'],remote)
        except BaseException:
            error=ProviderError('Overseas accepted task needs local reconciliation; never resubmit')
            error.overseas_recovery={'accepted_task_id':remote,'attempt_id':attempt['id'],
                'acceptance_receipt_file':str(receipt_file),'acceptance_receipt_sha256':receipt_sha,
                'receipt_persisted':receipt_persisted,'reservation_retained':True,'safe_to_resubmit':False}
            raise error from None
        return {'provider':PROVIDER,'task_identity':task_identity(remote),'task_id':remote,'attempt_id':row['id'],
                'operation':request['operation'],'operation_id':request['operation_id'],'state':'confirmed',
                'request_sha256':checksum,'reserved_credits':row['reserved_credits'],'uploads':uploads,
                'acceptance_receipt_file':str(receipt_file),'acceptance_receipt_sha256':receipt_sha}


def _download(manifest, job, transport, checkpoint):
    data,safe=_query(manifest,transport,checkpoint)
    if data['status']!='success':raise ProviderError('Overseas task output is not ready; no new generation')
    output=data.get('output');url=output.get('model_url') if isinstance(output,dict) else None
    if not isinstance(url,str) or not url:raise ProviderError('Overseas task has no supported model output')
    partial=job/'original-model.glb.partial';target=job/'original-model.glb'
    if partial.exists() or target.exists():raise ProviderError('Overseas download output already exists')
    response=connection=None;count=0;expected=None
    try:
        response,connection=transport.artifact(url)
        if response.status!=200:raise ProviderError('Overseas artifact response rejected')
        length=response.getheader('Content-Length')
        if length is not None:
            if not isinstance(length,str) or not length.isascii() or not length.isdecimal() or len(length)>12:
                raise ProviderError('Invalid overseas artifact length')
            expected=int(length)
            if not 1<=expected<=manifest['max_download_bytes']:raise ProviderError('Overseas artifact exceeds approved byte bound')
        with partial.open('xb') as stream:
            while True:
                checkpoint();block=response.read(min(1024*1024,manifest['max_download_bytes']-count+1))
                if not block:break
                count+=len(block)
                if count>manifest['max_download_bytes']:raise ProviderError('Overseas artifact exceeds approved byte bound')
                stream.write(block)
        if expected is not None and expected!=count:raise ProviderError('Overseas artifact length mismatch; partial retained')
        _validate_glb(partial,count,checkpoint)
        with partial.open('rb') as stream:checksum=hashlib.file_digest(stream,'sha256').hexdigest()
        checkpoint();partial.rename(target)
        return {**safe,'state':'downloaded','path':str(target),'bytes':count,'expected_bytes':expected,
                'sha256':checksum,'integrity':{'glb_container':'pass','publisher_hash':'not_provided'},
                'recovery':'Keep known overseas task; no automatic retry or CN checkpoint reuse'}
    except (ProviderError,ContractError):raise
    except Exception:raise ProviderError('Overseas download incomplete; partial retained; query the same task') from None
    finally:
        for resource in (response,connection):
            if resource is not None:
                try:resource.close()
                except Exception:pass


def run_manifest(manifest,job_directory,transport=None,checkpoint=lambda:None):
    manifest=normalize_manifest(manifest);job=Path(job_directory).resolve();job.mkdir(parents=True,exist_ok=True)
    transport=transport or OverseasHTTPTransport();checkpoint()
    action=manifest['operation']
    if action=='submit':return _submit(manifest,job,transport,checkpoint)
    if action=='query':return _query(manifest,transport,checkpoint)[1]
    if action=='download':return _download(manifest,job,transport,checkpoint)
    data=transport.api('/account/balance',manifest['credential_ref']['reference'])
    if not isinstance(data,dict) or 'balance' not in data or 'frozen' not in data:
        raise ProviderError('Invalid overseas balance response')
    result={'provider':PROVIDER,'region':REGION,'base_url':BASE,'balance':money(data['balance']),
            'frozen':money(data['frozen']),'accounting':'read_only_not_a_budget_authorization'}
    checkpoint();return result
