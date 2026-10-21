"""Bounded, secret-free API failure evidence; never a retry authorization.

Numeric codes: China v3 error-handling reference, read 2026-10-16; 1007
additionally follows the existing China rate-limit contract. Remote prose,
headers and URLs are deliberately not accepted by this module.
"""
import hashlib
import re
import socket
import ssl
import urllib.error

from .common import ProviderError


PROVIDER_CODES = {
    1000: 'invalid_api_key', 1001: 'unauthorized', 1007: 'api_frequency',
    2000: 'account_concurrency', 2002: 'unsupported_parameter',
    2003: 'empty_input_file', 2004: 'unsupported_file_type',
    2008: 'content_policy', 2010: 'insufficient_credits',
    2015: 'deprecated_version', 2018: 'model_too_complex',
}
TRANSPORT_CATEGORIES = frozenset(('timeout', 'tls', 'dns', 'connection',
                                 'os_error', 'invalid_response', 'unclassified'))


class SafeProviderError(ProviderError):
    """Only validated numbers, fixed labels and optional SHA may leave here.

The public worker may keep its existing VALIDATION_FAILED classification;
the fixed safe message also carries the useful HTTP/provider observations.
"""
    def __init__(self, *, http_status=None, provider_code=None,
                 request_id=None, category='unclassified'):
        status = http_status if type(http_status) is int and 100 <= http_status <= 599 else None
        code = provider_code if type(provider_code) is int and provider_code in PROVIDER_CODES else None
        classification = PROVIDER_CODES[code] if code is not None else (
            category if type(category) is str and category in TRANSPORT_CATEGORIES else 'unclassified')
        request_sha = None
        if type(request_id) is str and re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', request_id):
            request_sha = hashlib.sha256(request_id.encode('ascii')).hexdigest()
        self.details = {'http_status': status, 'provider_code': code,
                        'category': classification, 'request_id_sha256': request_sha,
                        'automatic_retry': False}
        observations = ['HTTP ' + str(status)] if status is not None else []
        if code is not None:
            observations.append('provider code ' + str(code))
        observations.append(classification)
        if request_sha is not None:
            observations.append('request_id_sha256 ' + request_sha)
        super().__init__('Provider API failure (' + '; '.join(observations) +
                         '); stopped without automatic retry; do not repeat a paid submission')


def response_failure(http_status, document=None):
    """Extract exactly two documented body fields; do not retain the body."""
    document = document if isinstance(document, dict) else {}
    return SafeProviderError(http_status=http_status, provider_code=document.get('code'),
                             request_id=document.get('request_id'))


def transport_failure(error, http_status=None):
    """Inspect exception types only, including one URLError reason level."""
    reason = error.reason if isinstance(error, urllib.error.URLError) else error
    category = 'unclassified'
    for kind, label in ((TimeoutError, 'timeout'), (ssl.SSLError, 'tls'),
                        (socket.gaierror, 'dns'), (ConnectionError, 'connection'),
                        (OSError, 'os_error')):
        if isinstance(reason, kind):
            category = label
            break
    return SafeProviderError(http_status=http_status, category=category)
