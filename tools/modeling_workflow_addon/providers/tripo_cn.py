"""China Tripo v3 adapter: six exercised shapes and explicit native parts.

Public blenderctl owns each explicit action or bounded batch. Task observation
in submit_batch uses an explicit GET budget; no paid retry, fallback or recharge.
"""
import ctypes
import hashlib
import http.client
import ipaddress
import json
import math
import os
from pathlib import Path
import socket
import ssl
import struct
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET
import zlib

from ..contract import credential_reference, identity, normalize_provider_request, request_sha256, path
from .budget import BudgetLedger, TrustedAuthorization, TrustedBinding, TERMINAL
from .common import ProviderError, RateLimitError, ArtifactAccessError, PathLocks, FileGuard, atomic_json
from protocol import Failure

BASE = 'https://openapi.tripo3d.com/v3'
MODELS = {'h31_image': 'v3.1-20260211', 'h31_multiview': 'v3.1-20260211',
          'h31_parts_multiview': 'v3.1-20260211',
          'p2_multiview': 'P2-20260801', 'segment_v2': 'v2.0-20260430', 'texture_v35': 'v3.5-20260815',
          'complete_all_v1': 'v1.0-20250506'}
ROUTES = {'h31_image': '/generation/image-to-model', 'h31_multiview': '/generation/multiview-to-model',
          'h31_parts_multiview': '/generation/multiview-to-model',
          'p2_multiview': '/generation/multiview-to-model', 'segment_v2': '/mesh/segment', 'texture_v35': '/models/texture',
          'complete_all_v1': '/mesh/complete'}
GENERATION = {'h31_image', 'h31_multiview', 'p2_multiview', 'h31_parts_multiview'}
# Historical standard request prices are a minimum reservation preflight,
# not an account balance, financial budget, or permanent current-price promise.
OBSERVED_CREDITS = {'h31_image': 20, 'h31_multiview': 20, 'p2_multiview': 100,
                    'segment_v2': 40, 'texture_v35': 20, 'complete_all_v1': 50}
# New parts floor is documented 20 geometry + 20 parts; H2 observed the same total separately.
# It is a reservation minimum, never a cloud hard debit limit or live quote.
RESERVATION_MINIMUM_CREDITS = {**OBSERVED_CREDITS, 'h31_parts_multiview': 40}
STATUSES = {'queued': 'queued', 'running': 'running', 'success': 'succeeded', 'failed': 'failed', 'cancelled': 'cancelled'}
# Observed in historical generation/segment/texture and C1 completion task receipts.
# H3.1/P2 multiview share a type: this cannot prove model/input correspondence.
TASK_TYPES = {'h31_image': 'image_to_model', 'h31_multiview': 'multiview_to_model',
              # Documented endpoint category, observed in the single H2 public runtime case.
              'h31_parts_multiview': 'multiview_to_model',
              'p2_multiview': 'multiview_to_model', 'segment_v2': 'mesh_segmentation',
              'texture_v35': 'texture_model', 'complete_all_v1': 'mesh_completion'}


def capabilities():
    return {'provider': 'tripo-cn', 'region': 'cn', 'base_url': BASE,
            'operations': [{'operation': op, 'model': model, 'route': ROUTES[op],
                            'evidence': ('H2 public submit/query/download/import/fresh reopen exercised once; semantic_quality partial_fail; texture_preservation not_run' if op == 'h31_parts_multiview' else
                                         'historical runtime exercised; shared adapter offline tested'),
                            **({'documented_minimum_credits': 40, 'runtime_status': 'public_runtime_one_case_with_limits',
                                'expected_task_type': TASK_TYPES[op],
                                'task_type_evidence': 'H2 receipt observed once on 2026-10-14',
                                'runtime_evidence': {'case': 'H2', 'verified_on': '2026-10-14',
                                    'task_type': 'multiview_to_model', 'observed_credits': 40,
                                    'observed_mesh_objects': 12, 'public_paid_submit': True,
                                    'runtime_not_run': False, 'semantic_quality': 'partial_fail',
                                    'texture_preservation': 'not_run', 'one_case_only': True}}
                               if op == 'h31_parts_multiview' else
                               {'historically_observed_credits': OBSERVED_CREDITS[op]}),
                            'parameters': ('texture=false,pbr=false; front PNG' if op == 'h31_image' else
                                           'texture=false,pbr=false,generate_parts=true; front/back PNG' if op == 'h31_parts_multiview' else
                                           'texture=false,pbr=false; front/back PNG' if op in GENERATION else
                                           'balanced,split_by_connectivity=true; original generation task' if op == 'segment_v2' else
                                           'ai_completion; all parts (part_names omitted); successful segment_v2 task' if op == 'complete_all_v1' else
                                           'detailed,pbr=true,bake=true,delight=true; original generation task; original style')}
                           for op, model in MODELS.items()],
            'actions': ['submit', 'submit_batch', 'query_known_task', 'query_batch', 'download_known_glb', 'download_batch', 'resume_download', 'bind_unknown'],
            'unsupported': ['selected-part completion', 'quick_cap', 'texture_prompt.image/images', 'text guidance', 'arbitrary parameters',
                            'other regions', 'format conversion', 'provider part-name inference', 'paid retry'],
            'limits': ['One outstanding financial reservation per ledger', 'No end-to-end family parity guarantee',
                       'Submit requires explicit approval descriptor/hash and input hashes',
                       'Live H3.1 submit/query/download verified; complete_all_v1 paid submit and reviewed unknown binding are offline-tested only',
                       'Range recovery requires a checkpoint, source SHA, known length and equal strong ETag; no automatic retries',
                       'Query batch: 1-16 known tasks, sequential one-pass GET; stops on query error or cancellation',
                       'HTTP 429: bounded delay hints only, no automatic retry; paid POST reservation remains unknown',
                       'Artifact HTTP 401/403/404/410 expose safe access failure, not proof of expiry; use a new same-task job',
                       'Observed task expired stops output recovery without financial settlement; refreshed URLs are not guaranteed',
                       'Submit batch: 1-8 approved requests, one original ledger owner, explicit task GET budget; accepted tasks never reposted',
                       'Download batch: 1-16 known-task items with per-item and total byte bounds; first failure stops']}


def _credential(reference):
    reference = credential_reference(reference)
    try:
        if reference['kind'] == 'environment':
            key = os.environ.get(reference['name'], '')
        else:
            if os.name != 'nt':
                raise ProviderError('DPAPI credential requires Windows')
            location = Path(reference['path'])
            if location.stat().st_size > 128 * 1024:
                raise ProviderError('Credential reference exceeds size bound')
            encrypted = location.read_bytes()
            encoding = 'utf-8'
            if reference['kind'] == 'dpapi_clixml':
                # A SecureString Export-Clixml file contains one namespaced SS
                # node of hex DPAPI ciphertext. Never evaluate PowerShell/XML.
                declaration_probe = encrypted.upper().replace(b'\0', b'')
                if b'<!DOCTYPE' in declaration_probe or b'<!ENTITY' in declaration_probe:
                    raise ProviderError('Unsupported credential XML declaration')
                root = ET.fromstring(encrypted)
                nodes = [node for node in root.iter() if node.tag == '{http://schemas.microsoft.com/powershell/2004/04}SS']
                if len(nodes) != 1 or list(nodes[0]) or nodes[0].text is None:
                    raise ProviderError('Expected one CLIXML SecureString')
                text = nodes[0].text.strip()
                if len(text) % 2 or not text or any(c not in '0123456789abcdefABCDEF' for c in text):
                    raise ProviderError('Invalid CLIXML SecureString ciphertext')
                encrypted = bytes.fromhex(text)
                encoding = 'utf-16-le'
            if not 1 <= len(encrypted) <= 65536:
                raise ProviderError('Invalid DPAPI credential size')
            class Blob(ctypes.Structure):
                _fields_ = [('size', ctypes.c_ulong), ('data', ctypes.POINTER(ctypes.c_ubyte))]
            buffer = (ctypes.c_ubyte * len(encrypted)).from_buffer_copy(encrypted)
            incoming = Blob(len(encrypted), buffer); outgoing = Blob()
            crypto = ctypes.WinDLL('crypt32', use_last_error=True)
            crypto.CryptUnprotectData.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(Blob)]
            crypto.CryptUnprotectData.restype = ctypes.c_int
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel.LocalFree.argtypes = [ctypes.c_void_p]; kernel.LocalFree.restype = ctypes.c_void_p
            if not crypto.CryptUnprotectData(ctypes.byref(incoming), None, None, None, None, 1, ctypes.byref(outgoing)):
                raise ProviderError('DPAPI credential unavailable for current user')
            try:
                key = ctypes.string_at(outgoing.data, outgoing.size).decode(encoding)
            finally:
                ctypes.memset(outgoing.data, 0, outgoing.size)
                kernel.LocalFree(outgoing.data)
        if not key or len(key) > 4096 or any(ord(c) < 33 or ord(c) > 126 for c in key):
            raise ProviderError('Credential is missing or invalid')
        return key
    except ProviderError:
        raise
    except Exception:
        raise ProviderError('Credential reference cannot be resolved') from None


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class HTTPTransport:
    """Production transport. No redirects, shared API/CDN opener or secret logs."""
    def upload_png(self, reference, raw, *, checkpoint=lambda: None):
        """Small authenticated presign, then bounded original-byte storage PUT."""
        from .image_upload import upload_png
        return upload_png(lambda route, payload: self.api(route, reference, payload),
                          raw, checkpoint=checkpoint)

    def api(self, route, reference, payload=None, content_type=None):
        from .transport_diagnostics import SafeProviderError, response_failure, transport_failure
        if not route.startswith('/') or '://' in route or '..' in route:
            raise ProviderError('Invalid provider route')
        key = _credential(reference)
        headers = {'Authorization': 'Bearer ' + key, 'Accept': 'application/json'}
        body = None
        if payload is not None:
            body = payload if isinstance(payload, bytes) else json.dumps(payload, allow_nan=False).encode('utf-8')
            headers['Content-Type'] = content_type or 'application/json'
        observed_status = None
        try:
            request = urllib.request.Request(BASE + route, data=body, headers=headers)
            with urllib.request.build_opener(NoRedirect()).open(request, timeout=45) as response:
                observed_status = getattr(response, 'status', None)
                raw = response.read(4 * 1024 * 1024 + 1)
                if len(raw) > 4 * 1024 * 1024:
                    raise SafeProviderError(http_status=observed_status, category='invalid_response')
            try:
                document = json.loads(raw)
            except Exception:
                raise SafeProviderError(http_status=observed_status, category='invalid_response') from None
            if not isinstance(document, dict):
                raise SafeProviderError(http_status=observed_status, category='invalid_response')
            if document.get('code') != 0:
                raise response_failure(observed_status, document) from None
            if not isinstance(document.get('data'), dict):
                raise SafeProviderError(http_status=observed_status, category='invalid_response')
            return document['data']
        except urllib.error.HTTPError as exc:
            if exc.code != 429:
                document = None
                try:
                    raw = exc.read(64 * 1024 + 1)
                    if len(raw) <= 64 * 1024:
                        document = json.loads(raw)
                except Exception:
                    pass
                finally:
                    try:
                        exc.close()
                    except Exception:
                        pass
                raise response_failure(exc.code, document) from None
            provider_code = None
            delays = []
            try:
                try:
                    raw = exc.read(64 * 1024)
                    document = json.loads(raw)
                    code = document.get('code') if isinstance(document, dict) else None
                    if type(code) is int and code in (1007, 2000):
                        provider_code = code
                except Exception:
                    pass
                for header in ('Retry-After', 'X-RateLimit-Reset'):
                    value = exc.headers.get(header) if exc.headers is not None else None
                    if not isinstance(value, str):
                        continue
                    value = value.strip()
                    if not value or len(value) > 20 or not value.isascii() or not value.isdecimal():
                        continue
                    delay = int(value)
                    if header == 'X-RateLimit-Reset':
                        delay = math.ceil(delay - time.time())
                    if 0 <= delay <= 86400:
                        delays.append(delay)
            except Exception:
                # Body/header parsing cannot leak arbitrary remote text or
                # turn an observed 429 into a different failure category.
                pass
            finally:
                try:
                    exc.close()
                except Exception:
                    # Cleanup must not replace safe classification with remote
                    # exception text (or hide an already observed HTTP 429).
                    pass
            raise RateLimitError(provider_code=provider_code,
                                 http_method='GET' if payload is None else 'POST',
                                 retry_after_seconds=max(delays) if delays else None) from None
        except ProviderError:
            raise
        except Exception as exc:
            # HTTP errors and unexpected bodies may include keys, tokens or URLs.
            raise transport_failure(exc, observed_status) from None
        finally:
            key = None
            headers.clear()

    def artifact(self, url, offset=0):
        """Resolve once, pin a public IP, verify TLS against the original hostname.

        This prevents public-DNS checks followed by a second private resolution.
        The CDN channel has no API Authorization header. Redirects are rejected.
        """
        connection = None
        try:
            if type(offset) is not int or not 0 <= offset <= 2 * 1024 ** 3:
                raise ProviderError('Invalid artifact range offset')
            parsed = urllib.parse.urlsplit(url)
            if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.port not in (None, 443) or parsed.fragment:
                raise ProviderError('Unsafe artifact location')
            addresses = socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)
            ips = [item[4][0] for item in addresses]
            if not ips or any(not ipaddress.ip_address(ip).is_global for ip in ips):
                raise ProviderError('Artifact address is not public')
            hostname = parsed.hostname
            selected = ips[0]
            class PinnedHTTPS(http.client.HTTPSConnection):
                def connect(self):
                    raw = socket.create_connection((selected, 443), self.timeout)
                    try:
                        self.sock = self._context.wrap_socket(raw, server_hostname=hostname)
                    except BaseException:
                        raw.close()
                        raise
            connection = PinnedHTTPS(hostname, timeout=60, context=ssl.create_default_context())
            route = urllib.parse.urlunsplit(('', '', parsed.path or '/', parsed.query, ''))
            headers = {'Accept': 'model/gltf-binary', 'Accept-Encoding': 'identity'}
            if offset:
                headers['Range'] = 'bytes=' + str(offset) + '-'
            connection.request('GET', route, headers=headers)
            response = connection.getresponse()
            if response.status in (401, 403, 404, 410):
                raise ArtifactAccessError(response.status)
            if response.status != (206 if offset else 200) or response.getheader('Content-Encoding') not in (None, 'identity'):
                raise ProviderError('Artifact response rejected; redirects are not followed')
            return response, connection
        except ArtifactAccessError:
            if connection:
                try:
                    connection.close()
                except Exception:
                    pass
            raise
        except Exception:
            if connection:
                try:
                    connection.close()
                except Exception:
                    pass
            raise ProviderError('Artifact connection unavailable or unsafe') from None


def safe_task(data):
    out = {'observed_at': time.time()}
    for field in ('task_id', 'type', 'status'):
        try:
            out[field] = identity(data.get(field))
        except ValueError:
            pass
    for field in ('progress', 'credits_consumed'):
        value = data.get(field)
        if type(value) in (int, float) and math.isfinite(value) and 0 <= value <= (100 if field == 'progress' else 1000000000):
            out[field] = value
    code = data.get('error_code')
    if type(code) is int:
        out['error_code'] = code
    elif isinstance(code, str) and code.isascii() and len(code) <= 64 and all(c.isupper() or c.isdigit() or c == '_' for c in code):
        out['error_code'] = code
    output = data.get('output')
    out['output_fields'] = [key for key in ('model_url', 'rendered_image_url') if isinstance(output, dict) and key in output]
    return out


def _png(row):
    try:
        location = Path(row['file'])
        if not 45 <= location.stat().st_size <= 20 * 1024 * 1024:
            raise ProviderError('Approved PNG input exceeds size bound')
        with location.open('rb') as stream:
            raw = stream.read(20 * 1024 * 1024 + 1)
        if not 45 <= len(raw) <= 20 * 1024 * 1024 or hashlib.sha256(raw).hexdigest() != row['expected_sha256'] or raw[:8] != b'\x89PNG\r\n\x1a\n':
            raise ProviderError('Approved PNG input hash/size/signature failed')
        position = 8; first = True; ended = False
        while position + 12 <= len(raw):
            size, chunk = struct.unpack('>I4s', raw[position:position + 8])
            end = position + 12 + size
            if end > len(raw) or (first and (chunk != b'IHDR' or size != 13)):
                raise ProviderError('Invalid PNG structure')
            crc = struct.unpack('>I', raw[end - 4:end])[0]
            if zlib.crc32(raw[position + 4:end - 4]) & 0xffffffff != crc:
                raise ProviderError('PNG checksum mismatch')
            first = False; position = end
            if chunk == b'IEND':
                ended = size == 0 and position == len(raw)
                break
        if not ended:
            raise ProviderError('PNG is incomplete')
        return raw
    except ProviderError:
        raise
    except Exception:
        raise ProviderError('Approved PNG input unavailable') from None


def _validate_glb(filename, downloaded, checkpoint=lambda: None):
    try:
        with Path(filename).open('rb') as stream:
            if struct.unpack('<4sII', stream.read(12)) != (b'glTF', 2, downloaded):
                raise ProviderError('Unexpected model container')
            position = 12; chunks = 0
            while position < downloaded:
                checkpoint()
                header = stream.read(8)
                if len(header) != 8:
                    raise ProviderError('Incomplete GLB chunk header')
                size, kind = struct.unpack('<I4s', header)
                position += 8
                if size % 4 or position + size > downloaded or (chunks == 0 and kind != b'JSON'):
                    raise ProviderError('Invalid GLB chunk extent')
                if chunks == 0:
                    if size > 64 * 1024 * 1024:
                        raise ProviderError('GLB JSON exceeds size bound')
                    document = json.loads(stream.read(size))
                    if not isinstance(document, dict) or document.get('asset', {}).get('version') != '2.0':
                        raise ProviderError('GLB JSON schema unsupported')
                    for field in ('buffers', 'images'):
                        for row in document.get(field, []):
                            uri = row.get('uri')
                            if uri is not None and (not isinstance(uri, str) or not uri.startswith('data:')):
                                raise ProviderError('External GLB dependencies require separate reviewed handling')
                else:
                    stream.seek(size, 1)
                position += size; chunks += 1
            if position != downloaded or chunks == 0:
                raise ProviderError('Incomplete GLB')
    except (ProviderError, Failure):
        raise
    except Exception:
        raise ProviderError('GLB validation failed; partial retained') from None


class TripoCNProvider:
    def __init__(self, credential_ref, ledger, transport=None):
        self.credential_ref = credential_reference(credential_ref)
        self.ledger = ledger if isinstance(ledger, BudgetLedger) else BudgetLedger(ledger)
        self.transport = transport or HTTPTransport()
        self.upload_receipts = []

    def _upload_reference(self, raw, *, checkpoint=lambda: None):
        from .image_upload import UploadError
        from .transport_diagnostics import SafeProviderError
        try:
            reply = self.transport.upload_png(self.credential_ref, raw, checkpoint=checkpoint)
            token = reply.get('file_token')
            if not isinstance(token, str) or not token or len(token) > 4096 or any(ord(c) < 33 for c in token):
                raise ProviderError('Provider did not return a valid upload token; reservation retained')
            if isinstance(reply.get('upload_receipt'), dict):
                self.upload_receipts.append(dict(reply['upload_receipt']))
            checkpoint()
            return token
        except (UploadError, SafeProviderError, RateLimitError, Failure, KeyboardInterrupt, SystemExit) as exc:
            exc.uploads = list(self.upload_receipts)
            raise
        except Exception:
            error = ProviderError('Image upload response unavailable; reservation retained')
            error.uploads = list(self.upload_receipts)
            raise error from None

    def _api(self, route, payload=None, content_type=None):
        from .transport_diagnostics import SafeProviderError
        try:
            data = self.transport.api(route, self.credential_ref, payload, content_type)
            if not isinstance(data, dict):
                raise ProviderError('Unsupported provider response')
            return data
        except (RateLimitError, SafeProviderError):
            raise
        except Exception:
            raise ProviderError('Provider response unavailable or invalid') from None

    def _submission_inputs(self, request, authorization):
        request = normalize_provider_request(request)
        if not isinstance(authorization, TrustedAuthorization):
            raise ProviderError('Submission requires caller-verified authorization')
        ledger = BudgetLedger(self.ledger.path, authorization)
        op = request['operation']
        policy = authorization.operation(request, request_sha256(request))
        if policy['max_credits'] < RESERVATION_MINIMUM_CREDITS[op]:
            raise ProviderError('Approved per-call reservation is below operation minimum price')
        files = [_png(row) for row in request.get('inputs', [])]
        if files and not authorization.document['allows_upload']:
            raise ProviderError('Image upload is outside approval')
        if op not in GENERATION:
            source = ledger.known_task(request['source_task_id'])
            source_operations = {'segment_v2'} if op == 'complete_all_v1' else GENERATION
            if source['state'] != 'succeeded' or source['operation'] not in source_operations:
                raise ProviderError('Postprocessing source must be a successful supported parent operation in this ledger')
        return request, ledger, policy, files

    def validate_submission(self, request, authorization):
        """Bounded local preflight only; does not upload or submit."""
        self._submission_inputs(request, authorization)

    def submit(self, request, authorization, *, checkpoint=lambda: None):
        checkpoint()
        self.upload_receipts = []
        request, ledger, policy, files = self._submission_inputs(request, authorization)
        op = request['operation']
        checkpoint()
        available = self._api('/account/balance').get('balance')
        if type(available) not in (int, float) or not math.isfinite(available) or available < policy['max_credits']:
            raise ProviderError('Available provider credits do not cover the approved reservation; no recharge')
        checkpoint()
        attempt = ledger.reserve(request, request_sha256(request))
        self.ledger = ledger
        body = {'model': MODELS[op]}
        # Reservation precedes every outbound action. Upload errors leave a
        # conservative reserved attempt; never silently repeat it.
        if files:
            tokens = []
            for raw in files:
                checkpoint()
                tokens.append(self._upload_reference(raw, checkpoint=checkpoint))
            body.update(texture=False, pbr=False)
            if op == 'h31_parts_multiview':
                body['generate_parts'] = True
            if op == 'h31_image':
                body['input'] = tokens[0]
            else:
                body['inputs'] = [{'front': tokens[0]}, {'back': tokens[1]}]
        elif op == 'segment_v2':
            body.update(input=request['source_task_id'], segmentation_granularity='balanced', split_by_connectivity=True)
        elif op == 'complete_all_v1':
            body.update(input=request['source_task_id'], completion_mode='ai_completion')
        else:
            body.update(input=request['source_task_id'], texture_quality='detailed', pbr=True, bake=True, delight=True)
        checkpoint()
        ledger.update(attempt['id'], 'submitting')
        try:
            data = self._api(ROUTES[op], body)
            task_id = identity(data.get('task_id'))
            row = ledger.update(attempt['id'], 'confirmed', task_id=task_id)
        except BaseException as exc:
            ledger.update(attempt['id'], 'unknown')
            if isinstance(exc, RateLimitError):
                raise RateLimitError(provider_code=exc.details['provider_code'],
                                     http_method=exc.details['http_method'],
                                     retry_after_seconds=exc.details['retry_after_seconds'],
                                     submission_unknown=True) from None
            raise ProviderError('Submission outcome unknown; reservation retained; never resubmit') from None
        return {'provider': 'tripo-cn', 'attempt_id': row['id'], 'task_id': task_id,
                'operation_id': request['operation_id'], 'operation': op, 'state': 'confirmed',
                'request_sha256': request_sha256(request), 'reserved_credits': row['reserved_credits'],
                'uploads': list(self.upload_receipts)}

    def _query(self, task_id):
        # An explicit task_id is sufficient for a read-only recovery. It does
        # not manufacture a billed attempt, reuse historical quota, or authorize
        # this external task as a parent for a new paid operation.
        row = None
        if self.ledger.path.exists():
            snapshot = self.ledger.snapshot()['ledger']
            matches = [r for r in snapshot['attempts'] if r['task_id'] == task_id]
            row = matches[0] if len(matches) == 1 else None
        data = self._api('/tasks/' + urllib.parse.quote(task_id, safe=''))
        if data.get('task_id') != task_id:
            raise ProviderError('Provider task identity/status mismatch; reservation retained')
        if data.get('status') == 'expired':
            # Output retention is not a financial success/failure receipt.
            # Do not settle or release a reservation from this observation.
            raise ArtifactAccessError(task_expired=True)
        if data.get('status') not in STATUSES:
            raise ProviderError('Provider task identity/status mismatch; reservation retained')
        # Native parts has no observed type receipt yet. An unexpected type
        # cannot authorize settlement or a successful generation parent.
        if row is not None and row['operation'] == 'h31_parts_multiview' and data.get('type') != TASK_TYPES[row['operation']]:
            raise ProviderError('Provider native parts task type unavailable or incompatible; reservation retained')
        safe = safe_task(data)
        safe['recovered_external_task'] = row is None
        state = STATUSES[data['status']]
        credits = safe.get('credits_consumed')
        accounting = math.ceil(credits) if credits is not None and state in TERMINAL else None
        proof = hashlib.sha256(json.dumps(safe, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
        if row is not None:
            self.ledger.update(row['id'], state, accounted_credits=accounting, receipt_sha256=proof if accounting is not None else None)
        return data, safe

    def query(self, task_id):
        return self._query(identity(task_id))[1]

    def bind_unknown(self, request, binding):
        """Reconcile an operator-verified mapping with one GET and a local CAS.

        Task type, timestamps and credits never infer request correspondence.
        This operation does not settle cost or create another submission.
        """
        request = normalize_provider_request(request)
        row = self.ledger.binding_snapshot(request, binding)
        task_id = binding.document['task_id']
        data = self._api('/tasks/' + urllib.parse.quote(task_id, safe=''))
        status = data.get('status')
        if data.get('task_id') != task_id or not isinstance(status, str) or status not in STATUSES:
            raise ProviderError('Binding remote task identity/status mismatch; reservation retained')
        if data.get('type') != TASK_TYPES[request['operation']]:
            raise ProviderError('Binding remote task type unavailable or incompatible; reservation retained')
        snapshot = safe_task(data)
        proof = hashlib.sha256(json.dumps(snapshot, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
        bound = self.ledger.bind_unknown(request, binding, task_id, proof)
        return {'provider': 'tripo-cn', 'operation': 'bind_unknown', 'attempt_id': row['id'],
                'operation_id': row['operation_id'], 'task_id': task_id, 'state': bound['state'],
                'remote_status': status, 'reserved_credits': bound['reserved_credits'],
                'binding_sha256': binding.binding_sha256, 'followup_query_required': True,
                'accounting_deferred': True, 'remote_snapshot': snapshot,
                'remote_snapshot_sha256': proof}

    def download(self, task_id, target, max_bytes=2 * 1024 ** 3, *, recovery=None, partial_descriptor=None, checkpoint=lambda: None):
        from .download import download
        return download(self, task_id, target, max_bytes, recovery=recovery,
                        partial_descriptor=partial_descriptor, checkpoint=checkpoint)

    def resume_pre_submit(self, request, authorization, recovery, authorization_descriptor,
                          audit_directory, *, checkpoint=lambda: None):
        from .pre_submit_recovery import resume
        return resume(self, request, authorization, recovery, authorization_descriptor,
                      audit_directory, checkpoint=checkpoint)


def run_manifest(manifest, job_directory, transport=None, checkpoint=lambda: None):
    """Public job worker entry. Never persists request bodies or signed URLs."""
    from ..contract import normalize_manifest
    manifest = normalize_manifest(manifest)
    action = manifest['operation']
    if action == 'import_local':
        raise ProviderError('Local imports belong to the Blender candidate worker')
    if action == 'submit_batch':
        from .batch import run_submit
        return run_submit(manifest, job_directory, transport, checkpoint)
    if action == 'resume_pre_submit':
        from .pre_submit_recovery import TrustedPreSubmitRecovery
        authorization = TrustedAuthorization.from_descriptor(manifest['authorization_descriptor'])
        # Existing-only constructor; never initialize a lost financial ledger.
        provider = TripoCNProvider(manifest['credential_ref'], BudgetLedger(manifest['ledger_file']), transport)
        return provider.resume_pre_submit(manifest['request'], authorization,
            TrustedPreSubmitRecovery.from_descriptor(manifest['pre_submit_recovery_descriptor']),
            manifest['authorization_descriptor'], str(Path(job_directory).resolve()), checkpoint=checkpoint)
    authorization = TrustedAuthorization.from_descriptor(manifest['authorization_descriptor']) if action == 'submit' else None
    ledger = BudgetLedger(manifest['ledger_file'], authorization)
    provider = TripoCNProvider(manifest['credential_ref'], ledger, transport)
    if action == 'download_batch':
        from .batch import run_download
        return run_download(provider, manifest, job_directory, checkpoint)
    if action == 'submit':
        return provider.submit(manifest['request'], authorization, checkpoint=checkpoint)
    if action == 'bind_unknown':
        return provider.bind_unknown(manifest['request'], TrustedBinding.from_descriptor(manifest['binding_descriptor']))
    if action == 'query':
        return provider.query(manifest['task_id'])
    if action == 'query_batch':
        from .query_batch import run
        return run(provider, manifest['task_ids'], job_directory, checkpoint)
    return provider.download(manifest['task_id'], Path(job_directory).resolve() / 'original-model.glb', manifest['max_bytes'],
                             recovery=manifest.get('recovery_descriptor'), partial_descriptor=manifest.get('partial_descriptor'),
                             checkpoint=checkpoint)
