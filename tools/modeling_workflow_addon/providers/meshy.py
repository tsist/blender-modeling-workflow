"""Meshy known-task GET adapter. No POST/DELETE, uploads, ledger, or retries."""
import json
import urllib.error
import urllib.request
from .common import ProviderError, RateLimitError
from .registry import MESHY_ROUTES
from ..contract import credential_reference, identity

BASE = 'https://api.meshy.ai/openapi'
STATES = {'PENDING':'queued', 'IN_PROGRESS':'running', 'SUCCEEDED':'succeeded',
          'FAILED':'failed', 'CANCELED':'cancelled'}
FORMATS = {'glb','gltf','fbx','obj','mtl','usdz','stl','3mf','blend','vox'}


def task_schema(remote_operation=None):
    from ..contract import meshy_query_schema
    return meshy_query_schema(remote_operation)


class HTTPTransport:
    def api(self, route, credential_ref):
        from .tripo_cn import _credential, NoRedirect
        matches = [prefix for prefix in MESHY_ROUTES.values() if isinstance(route,str) and route.startswith(prefix)]
        if len(matches) != 1:
            raise ProviderError('Unsupported Meshy task GET route')
        task_id = identity(route[len(matches[0]):])
        if task_id in ('.','..'):
            raise ProviderError('Meshy task identity cannot be a URI dot segment')
        key = _credential(credential_ref)
        headers = {'Authorization':'Bearer ' + key, 'Accept':'application/json'}
        try:
            req = urllib.request.Request(BASE + route, headers=headers, method='GET')
            with urllib.request.build_opener(NoRedirect()).open(req, timeout=45) as response:
                raw = response.read(4 * 1024 * 1024 + 1)
                if len(raw) > 4 * 1024 * 1024:
                    raise ProviderError('Meshy task response exceeds size bound')
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise ProviderError('Unsupported Meshy task response')
            return value
        except urllib.error.HTTPError as exc:
            code = exc.code
            delay = None
            try:
                raw_delay = exc.headers.get('Retry-After') if exc.headers else None
                if isinstance(raw_delay, str) and raw_delay.isascii() and raw_delay.isdecimal() and len(raw_delay) <= 5:
                    number = int(raw_delay)
                    delay = number if 0 <= number <= 86400 else None
            except Exception:
                pass
            finally:
                try:
                    exc.close()
                except Exception:
                    pass
            if code == 429:
                raise RateLimitError(http_method='GET', retry_after_seconds=delay) from None
            raise ProviderError('Meshy task request unavailable; no automatic retry') from None
        except ProviderError:
            raise
        except Exception:
            raise ProviderError('Meshy task request unavailable; no automatic retry') from None
        finally:
            key = None
            headers.clear()


def query(manifest, transport=None):
    from ..contract import normalize_manifest
    manifest = normalize_manifest(manifest)
    if manifest.get('provider') != 'meshy' or manifest['operation'] != 'query':
        raise ProviderError('Meshy adapter only supports an explicit known-task query')
    task = identity(manifest['task_id'])
    route = MESHY_ROUTES[manifest['remote_operation']] + task
    try:
        value = (transport or HTTPTransport()).api(route, credential_reference(manifest['credential_ref']))
    except ProviderError:
        raise
    except Exception:
        raise ProviderError('Meshy task request unavailable or invalid') from None
    if not isinstance(value, dict) or value.get('id') != task or not isinstance(value.get('status'), str) or value['status'] not in STATES:
        raise ProviderError('Meshy task identity/status mismatch')
    progress = value.get('progress')
    if type(progress) is not int or not 0 <= progress <= 100:
        raise ProviderError('Invalid Meshy task progress')
    urls = value.get('model_urls')
    formats = sorted(k for k,v in urls.items() if k in FORMATS and isinstance(v,str) and v) if isinstance(urls,dict) else []
    return {'provider':'meshy', 'task_id':task, 'remote_operation':manifest['remote_operation'],
            'task_identity':{'provider':'meshy','task_id':task,'remote_operation':manifest['remote_operation']},
            'status':STATES[value['status']], 'native_status':value['status'], 'progress':progress,
            'available_output_formats':formats, 'recovered_external_task':True,
            'accounting':'not_observed; no financial ledger touched',
            'limits':['GET only; no remote cancellation, refund, download, or generation claim',
                      'Provider error messages and signed artifact URLs are omitted']}
