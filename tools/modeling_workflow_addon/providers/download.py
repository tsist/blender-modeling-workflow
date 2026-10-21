"""Bounded download checkpoints and explicit, read-only source recovery.

Only equal strong ETag digests plus an exact single Content-Range permit
combining responses (RFC 9110 15.3.7.3). Signed URLs are never persisted.
"""
from contextlib import ExitStack
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import urllib.parse

from ..contract import canonical_path, file_descriptor, identity, integer, obj, sha
from .common import ProviderError, ArtifactAccessError, PathLocks, FileGuard, atomic_json
from protocol import Failure

BLOCK = 1024 * 1024
LIMIT = 2 * 1024 ** 3


def etag_digest(value):
    # Weak/missing/malformed validators never enable byte-range recovery.
    if not isinstance(value, str) or not re.fullmatch(r'"[\x21\x23-\x7e]{0,1022}"', value):
        return None
    return hashlib.sha256(value.encode('ascii')).hexdigest()


def length_header(response):
    value = response.getheader('Content-Length')
    if value is None:
        return None
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]{1,10}', value):
        raise ProviderError('Invalid artifact Content-Length')
    return int(value)


def resource_digest(url):
    """Scope ETag equality to the same task output and HTTPS origin/path.

    Expiring signature queries may change. Different CDN paths/origins fail
    closed even if they happen to reuse the same ETag.
    """
    value = urllib.parse.urlsplit(url)
    if value.scheme != 'https' or not value.hostname or value.username or value.password or value.fragment or value.port not in (None, 443):
        raise ProviderError('Unsafe artifact identity')
    data = ['https', value.hostname.lower(), 443, value.path or '/']
    return hashlib.sha256(json.dumps(data, separators=(',', ':')).encode()).hexdigest()


def read_recovery(recovery, partial, task_id, max_bytes, stack, checkpoint):
    recovery, partial = file_descriptor(recovery), file_descriptor(partial)
    for row in (recovery, partial):
        stack.enter_context(FileGuard(Path(row['file']).parent, directory=True))
    metadata_guard = stack.enter_context(FileGuard(recovery['file']))
    if Path(recovery['file']).stat().st_size > 16384 or metadata_guard.sha256(checkpoint) != recovery['expected_sha256']:
        raise ProviderError('Recovery checkpoint changed or exceeds bound')
    try:
        meta = json.loads(b''.join(metadata_guard.chunks(checkpoint)))
        fields = ('schema_version', 'provider', 'task_id', 'partial_file', 'bytes',
                  'prefix_sha256', 'total_bytes', 'etag_sha256', 'resource_sha256')
        obj(meta, fields, fields)
        if meta['schema_version'] != '1.0' or meta['provider'] != 'tripo-cn' or identity(meta['task_id']) != task_id:
            raise ProviderError('Recovery checkpoint task/provider mismatch')
        if canonical_path(meta['partial_file']) != canonical_path(partial['file']):
            raise ProviderError('Recovery checkpoint does not describe the supplied partial')
        if canonical_path(recovery['file']) != canonical_path(partial['file'] + '.json'):
            raise ProviderError('Recovery checkpoint must accompany its original partial')
        count = integer(meta['bytes'], 1, max_bytes)
        total = integer(meta['total_bytes'], 12, max_bytes)
        sha(meta['prefix_sha256'])
        sha(meta['resource_sha256'])
        if count > total:
            raise ProviderError('Recovery prefix exceeds original length')
        if meta['etag_sha256'] is not None:
            sha(meta['etag_sha256'])
        if count < total and not meta['etag_sha256']:
            raise ProviderError('Range recovery requires an original strong ETag and known length')
    except (ProviderError, Failure):
        raise
    except Exception:
        raise ProviderError('Unsupported recovery checkpoint') from None
    source = stack.enter_context(FileGuard(partial['file']))
    if Path(partial['file']).stat().st_size > max_bytes or source.sha256(checkpoint) != partial['expected_sha256']:
        raise ProviderError('Recovery partial changed or exceeds bound')
    prefix = hashlib.sha256(); remaining = count
    for block in source.chunks(checkpoint):
        take = block[:remaining]
        prefix.update(take); remaining -= len(take)
        if not remaining:
            break
    if remaining or prefix.hexdigest() != meta['prefix_sha256']:
        raise ProviderError('Recovery prefix checksum/length mismatch')
    return meta, source


def download(provider, task_id, target, max_bytes, *, recovery=None, partial_descriptor=None, checkpoint=lambda: None):
    from .tripo_cn import _validate_glb
    task_id = identity(task_id)
    target = Path(target)
    if not target.is_absolute() or target.suffix.lower() != '.glb' or type(max_bytes) is not int or not 12 <= max_bytes <= LIMIT:
        raise ProviderError('Expected absolute GLB target and bounded maximum bytes')
    if (recovery is None) != (partial_descriptor is None):
        raise ProviderError('Recovery requires both checkpoint and partial descriptors')
    partial = target.with_name(target.name + '.partial')
    state_file = partial.with_name(partial.name + '.json')
    target.parent.mkdir(parents=True, exist_ok=True)
    with ExitStack() as stack:
        stack.enter_context(FileGuard(target.parent, directory=True))
        stack.enter_context(PathLocks([target, partial, state_file]))
        if any(os.path.lexists(p) for p in (target, partial, state_file)):
            raise ProviderError('Target/partial/checkpoint already exists; use a new recovery job')
        checkpoint()
        meta = source = None
        if recovery is not None:
            meta, source = read_recovery(recovery, partial_descriptor, task_id, max_bytes, stack, checkpoint)
        offset = meta['bytes'] if meta else 0
        expected = meta['total_bytes'] if meta else None
        etag = meta['etag_sha256'] if meta else None
        resource = meta['resource_sha256'] if meta else None
        connection = response = None
        checksum = hashlib.sha256(); downloaded = 0
        try:
            # A fully checkpointed file needs only local validation, no network.
            if not meta or offset < expected:
                data, safe = provider._query(task_id)
                checkpoint()
                output = data.get('output')
                url = output.get('model_url') if isinstance(output, dict) else None
                if data['status'] != 'success' or not isinstance(url, str):
                    raise ProviderError('Successful model output unavailable')
                current_resource = resource_digest(url)
                if meta and current_resource != resource:
                    raise ProviderError('Artifact resource changed; original partial retained')
                resource = current_resource
                try:
                    if offset:
                        response, connection = provider.transport.artifact(url, offset=offset)
                    else:
                        response, connection = provider.transport.artifact(url)
                finally:
                    url = None
                checkpoint()
                if getattr(response, 'status', None) in (401, 403, 404, 410):
                    raise ArtifactAccessError(response.status)
                if response.getheader('Content-Encoding') not in (None, 'identity'):
                    raise ProviderError('Encoded artifacts cannot be resumed or verified')
                observed_etag = etag_digest(response.getheader('ETag'))
                length = length_header(response)
                if offset:
                    match = re.fullmatch(r'bytes ([0-9]{1,10})-([0-9]{1,10})/([0-9]{1,10})', response.getheader('Content-Range') or '')
                    if (getattr(response, 'status', None) != 206 or not match or
                            tuple(map(int, match.groups())) != (offset, expected - 1, expected) or
                            length != expected - offset or observed_etag != etag):
                        raise ProviderError('Range/length/strong ETag mismatch; original partial retained')
                    if (response.getheader('Content-Type') or '').lower().startswith('multipart/'):
                        raise ProviderError('Multipart range responses are unsupported')
                else:
                    if getattr(response, 'status', 200) != 200 or response.getheader('Content-Range') is not None:
                        raise ProviderError('Expected a complete artifact response')
                    expected, etag = length, observed_etag
                    if expected is not None and not 12 <= expected <= max_bytes:
                        raise ProviderError('Artifact length outside approved bound')

            def persist(stream):
                stream.flush(); os.fsync(stream.fileno())
                atomic_json(state_file, {'schema_version': '1.0', 'provider': 'tripo-cn',
                    'task_id': task_id, 'partial_file': str(partial), 'bytes': downloaded,
                    'prefix_sha256': checksum.hexdigest(), 'total_bytes': expected,
                    'etag_sha256': etag, 'resource_sha256': resource})

            def write_block(stream, block):
                nonlocal downloaded
                if downloaded + len(block) > max_bytes or (expected is not None and downloaded + len(block) > expected):
                    raise ProviderError('Artifact exceeded byte bound; partial retained')
                stream.write(block); checksum.update(block); downloaded += len(block)

            with partial.open('xb') as stream:
                if source:
                    remaining = offset
                    for block in source.chunks(checkpoint):
                        take = block[:remaining]
                        write_block(stream, take); remaining -= len(take)
                        if not remaining:
                            break
                persist(stream)
                if response:
                    while True:
                        checkpoint()
                        try:
                            block = response.read(BLOCK)
                        except http.client.IncompleteRead as exc:
                            write_block(stream, exc.partial)
                            persist(stream)
                            raise ProviderError('Artifact interrupted; checkpoint retained') from None
                        if not block:
                            break
                        write_block(stream, block)
                        persist(stream)
                        checkpoint()
            if not downloaded or (expected is not None and downloaded != expected):
                raise ProviderError('Artifact incomplete; partial retained')
            checkpoint()
            _validate_glb(partial, downloaded, checkpoint)
            with FileGuard(partial, renameable=True) as guard:
                if guard.sha256(checkpoint) != checksum.hexdigest():
                    raise ProviderError('Downloaded artifact changed before promotion')
                checkpoint()
                guard.rename_new(target)
        except (ProviderError, Failure):
            raise
        except Exception:
            raise ProviderError('Artifact failed; inspect retained checkpoint; no automatic retry') from None
        finally:
            if connection:
                # Closing a completed/failed HTTP exchange must never replace
                # the cancellation/timeout verdict or expose a raw URL error.
                try:
                    connection.close()
                except Exception:
                    pass
        return {'state': 'complete', 'provider': 'tripo-cn', 'task_id': task_id, 'path': str(target),
                'bytes': downloaded, 'expected_bytes': expected, 'sha256': checksum.hexdigest(),
                'resumed_bytes': offset, 'resumed_from': partial_descriptor if meta else None,
                'checkpoint_file': str(state_file),
                'integrity': 'HTTPS; length when provided; complete GLB structure; local SHA, no publisher SHA; ranges require equal strong ETag'}
