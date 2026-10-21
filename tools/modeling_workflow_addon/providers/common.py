"""Reuse the public CLI's atomic metadata and cross-process path locks."""
from pathlib import Path
import sys

CLI_ROOT = Path(__file__).resolve().parents[2] / 'blenderctl'
if str(CLI_ROOT) not in sys.path:
    sys.path.insert(0, str(CLI_ROOT))
from protocol import atomic_json, digest, read_json
from filesystem import PathLocks, FileGuard


class ProviderError(ValueError):
    """Only fixed, secret-free messages may cross the adapter boundary."""


class ArtifactAccessError(ProviderError):
    """Observed access failure; an HTTP status alone does not prove expiry."""

    code = 'ARTIFACT_UNAVAILABLE'

    def __init__(self, http_status=None, *, task_expired=False):
        if task_expired is True:
            self.details = {'observed_stage': 'task_query', 'http_status': None,
                            'remote_status': 'expired', 'expiration_confirmed': True,
                            'accounting_updated': False, 'automatic_retry': False,
                            'recovery_action': 'inspect_local_files_or_provider_retention'}
            message = 'Remote task output expired; inspect existing local files; do not submit again'
        else:
            if type(http_status) is not int or http_status not in (401, 403, 404, 410):
                raise ProviderError('Unsupported artifact access failure status')
            self.details = {'observed_stage': 'artifact_http', 'http_status': http_status,
                            'expiration_confirmed': False, 'automatic_retry': False,
                            'recovery_action': 'query_same_task_in_new_job'}
            message = 'Artifact access unavailable; use a new download job for the same task; no automatic retry'
        super().__init__(message)


class RateLimitError(ProviderError):
    """Fixed 429 evidence only; retry hints never grant automatic retry."""

    code = 'RATE_LIMITED'

    def __init__(self, *, provider_code=None, http_method='GET', retry_after_seconds=None,
                 submission_unknown=False):
        code = provider_code if type(provider_code) is int and provider_code in (1007, 2000) else None
        method = http_method if http_method in ('GET', 'POST') else 'GET'
        delay = (retry_after_seconds if type(retry_after_seconds) is int
                 and 0 <= retry_after_seconds <= 86400 else None)
        self.details = {'http_status': 429, 'provider_code': code,
                        'category': {1007: 'api_frequency', 2000: 'account_concurrency'}.get(code, 'unspecified'),
                        'http_method': method, 'automatic_retry': False, 'retry_after_seconds': delay}
        message = 'Provider rate limited (HTTP 429); stopped without automatic retry'
        if submission_unknown:
            self.details.update(submission_state='unknown', reservation_retained=True, safe_to_resubmit=False)
            message = 'Submission outcome unknown after HTTP 429; reservation retained; never resubmit'
        super().__init__(message)
