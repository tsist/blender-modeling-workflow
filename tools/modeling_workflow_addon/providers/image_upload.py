"""Bounded PNG upload through the provider's official presigned storage route.

The authenticated API callback is used once for a small presign request. Storage
uses an independent, pinned HTTPS connection without API credentials, redirects,
retries or response-body logging. Local send completion is not a remote receipt.
"""
import hashlib
import http.client
import ipaddress
import math
import queue
import socket
import ssl
import threading
import time
import urllib.parse

from .common import ProviderError, RateLimitError
from .transport_diagnostics import SafeProviderError

MAX_PNG_BYTES = 20 * 1024 * 1024
CHUNK_BYTES = 16 * 1024
IDLE_TIMEOUT_SECONDS = 30.0
TOTAL_TIMEOUT_SECONDS = 180.0


class _UnsafeAddress(ValueError):
    pass


class UploadError(ProviderError):
    """Only fixed categories and numeric upload observations leave the transport."""
    def __init__(self, details):
        self.details = dict(details)
        super().__init__('Image upload stopped (' + self.details['stage'] + ': ' +
                         self.details['category'] + '); no automatic retry')


def _connect_tcp(address, timeout):
    return socket.create_connection(address, timeout)


class _PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host, address, timeout):
        super().__init__(host, port=443, timeout=timeout, context=ssl.create_default_context())
        self._pinned_address = address

    def connect(self):
        # Numeric address bypasses DNS here. Certificate/SNI still use the
        # original hostname; no second hostname resolution can rebind the peer.
        raw = _connect_tcp((self._pinned_address, 443), self.timeout)
        try:
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except BaseException:
            raw.close()
            raise


def _resolve_public(host, deadline, checkpoint):
    """Bound DNS waiting without leaving a blocking resolver on the main thread."""
    result = queue.Queue(maxsize=1)
    def resolve():
        try:
            result.put((True, socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)))
        except Exception:
            result.put((False, None))
    threading.Thread(target=resolve, daemon=True, name='modeling-upload-dns').start()
    while True:
        checkpoint()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError()
        try:
            okay, rows = result.get(timeout=min(0.1, remaining))
            break
        except queue.Empty:
            continue
    if not okay:
        raise socket.gaierror()
    if not rows:
        raise _UnsafeAddress()
    addresses = []
    for row in rows:
        address = row[4][0]
        if not ipaddress.ip_address(address).is_global:
            raise _UnsafeAddress()
        addresses.append((row[0], address))
    return next((address for family, address in addresses if family == socket.AF_INET), addresses[0][1])


def _presigned(document):
    if not isinstance(document, dict):
        raise ValueError()
    token, url = document.get('file_token'), document.get('presigned_url')
    if (not isinstance(token, str) or not 1 <= len(token) <= 4096 or
            any(ord(c) < 33 or ord(c) > 126 for c in token)):
        raise ValueError()
    if (not isinstance(url, str) or not 1 <= len(url) <= 16384 or
            any(ord(c) < 33 or ord(c) > 126 for c in url)):
        raise ValueError()
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.port not in (None, 443)
            or parsed.username is not None or parsed.password is not None or parsed.fragment):
        raise ValueError()
    expires = document.get('expires_in')
    if 'expires_in' in document and (type(expires) not in (int, float) or
            not math.isfinite(expires) or expires <= 0):
        raise ValueError()
    route = urllib.parse.urlunsplit(('', '', parsed.path or '/', parsed.query, ''))
    return token, parsed.hostname, route, expires


def upload_png(api, raw, *, checkpoint=lambda: None, observer=None):
    """Return a token only after all original bytes and storage HTTP 200.

    ``api('/files/presign', {'format': 'png'})`` must itself be a bounded,
    authenticated small-JSON call (the existing provider API timeout is 45 s).
    Observer events contain only fixed stages and numeric/SHA evidence. Observer
    errors stop the operation. Exceptions raised by checkpoint propagate intact.
    """
    started = time.monotonic()
    deadline = started + TOTAL_TIMEOUT_SECONDS
    stage, sent, status, conn, timer = 'input', 0, None, None, None
    in_checkpoint = False
    raw_sha = None
    expiry_observed = False
    active_timeout_category = 'idle_timeout'

    def details(category=None):
        value = {'stage': stage, 'method': 'PUT', 'total_bytes': len(raw) if isinstance(raw, bytes) else 0,
                 'locally_completed_send_bytes': sent, 'http_status': status,
                 'elapsed_seconds': round(max(0, time.monotonic() - started), 6),
                 'input_sha256': raw_sha, 'expiry_observed': expiry_observed,
                 'automatic_retry': False}
        if category:
            value['category'] = category
        return value

    def fail(category):
        raise UploadError(details(category)) from None

    def check():
        nonlocal in_checkpoint
        in_checkpoint = True
        checkpoint()
        in_checkpoint = False
        if time.monotonic() >= deadline:
            fail('total_deadline')

    def emit():
        if observer is not None:
            try:
                observer(details())
            except Exception:
                fail('observer_failure')

    def timeout():
        nonlocal active_timeout_category
        check()
        remaining = deadline - time.monotonic()
        # Remember the limiting budget instead of relying on the exception
        # being observed after an exactly aligned monotonic clock edge.
        active_timeout_category = ('total_deadline' if remaining <= IDLE_TIMEOUT_SECONDS
                                   else 'idle_timeout')
        return max(0.001, min(IDLE_TIMEOUT_SECONDS, remaining))

    def abort_socket():
        # An absolute wall timer interrupts trickled response headers, where a
        # per-read socket timeout alone would keep resetting indefinitely.
        try:
            if conn is not None and conn.sock is not None:
                conn.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    try:
        check()
        if not isinstance(raw, bytes) or not 45 <= len(raw) <= MAX_PNG_BYTES or raw[:8] != b'\x89PNG\r\n\x1a\n':
            fail('invalid_png')
        raw_sha = hashlib.sha256(raw).hexdigest()
        stage = 'presign'
        check()
        emit()
        document = api('/files/presign', {'format': 'png'})
        check()
        try:
            token, host, route, expires = _presigned(document)
        except Exception:
            fail('invalid_presign')
        expiry_observed = expires is not None
        if expires is not None:
            deadline = min(deadline, time.monotonic() + expires)
        document = None
        stage = 'resolve'
        check()
        try:
            address = _resolve_public(host, deadline, check)
        except _UnsafeAddress:
            fail('unsafe_address')
        check()
        emit()
        stage = 'connect'
        conn = _PinnedHTTPS(host, address, timeout())
        timer = threading.Timer(max(0.001, deadline - time.monotonic()), abort_socket)
        timer.daemon = True
        timer.start()
        conn.connect()
        check()
        emit()
        stage = 'send_headers'
        conn.sock.settimeout(timeout())
        conn.putrequest('PUT', route, skip_accept_encoding=True)
        conn.putheader('Content-Type', 'application/octet-stream')
        conn.putheader('Content-Length', str(len(raw)))
        conn.putheader('Connection', 'close')
        conn.endheaders()
        check()
        emit()
        stage = 'send_body'
        for offset in range(0, len(raw), CHUNK_BYTES):
            check()
            conn.sock.settimeout(timeout())
            block = raw[offset:offset + CHUNK_BYTES]
            conn.send(block)
            sent += len(block)
            check()
            emit()
        stage = 'response_headers'
        check()
        conn.sock.settimeout(timeout())
        response = conn.getresponse()
        status = response.status if type(response.status) is int else None
        check()
        emit()
        # The tested storage contract returns 200 with an empty body. Closing
        # without reading also prevents remote error bodies entering diagnostics.
        if status != 200 or sent != len(raw):
            fail('http_rejected')
        stage = 'complete'
        check()
        emit()
        return {'file_token': token, 'upload_receipt': details()}
    except (UploadError, RateLimitError, SafeProviderError):
        raise
    except Exception as exc:
        if in_checkpoint:
            raise
        if time.monotonic() >= deadline:
            fail('total_deadline')
        if isinstance(exc, (TimeoutError, socket.timeout)):
            fail(active_timeout_category)
        if isinstance(exc, ssl.SSLError):
            fail('tls_failure')
        if isinstance(exc, socket.gaierror):
            fail('dns_failure')
        if isinstance(exc, (ConnectionError, OSError, http.client.HTTPException)):
            fail('connection_failure')
        fail('invalid_response')
    finally:
        if timer is not None:
            timer.cancel()
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
