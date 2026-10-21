"""Separate approval-bound overseas ledger; never migrates or opens China accounts."""
from copy import deepcopy
from dataclasses import dataclass
import hashlib
from pathlib import Path
import time
import uuid

from ..contract import canonical_path, file_descriptor, identity, integer, obj, sha
from .common import ProviderError, PathLocks, FileGuard, atomic_json, read_json
from .overseas_contract import (PROVIDER, REGION, BASE, OPERATIONS, money, money_cents,
                                normalize_request, request_sha256, task_id as validate_task_id)

TERMINAL = frozenset(('succeeded', 'failed', 'cancelled'))
STATES = frozenset(('reserved', 'submitting', 'upload_unresolved', 'unknown',
                    'confirmed', 'queued', 'running')) | TERMINAL


@dataclass(frozen=True)
class OverseasAuthorization:
    document: dict
    approval_sha256: str

    @classmethod
    def from_descriptor(cls, descriptor):
        try:
            descriptor = file_descriptor(descriptor)
            with PathLocks([descriptor['file']]), FileGuard(descriptor['file']) as guard:
                checksum = hashlib.sha256(); count = 0
                for block in guard.chunks():
                    count += len(block)
                    if count > 256 * 1024:
                        raise ProviderError('Overseas authorization exceeds size bound')
                    checksum.update(block)
                if not count or checksum.hexdigest() != descriptor['expected_sha256']:
                    raise ProviderError('Overseas authorization changed')
                document = read_json(descriptor['file'])
            cls.validate(document)
            document = deepcopy(document)
            document['ledger_file'] = canonical_path(document['ledger_file'])
            document['total_cap_credits'] = money(document['total_cap_credits'])
            for row in document['operations']:
                row['max_credits'] = money(row['max_credits'])
            return cls(document, descriptor['expected_sha256'])
        except ProviderError:
            raise
        except Exception:
            raise ProviderError('Overseas authorization unavailable or invalid') from None

    @staticmethod
    def validate(value):
        try:
            fields = ('schema_version', 'approved', 'approval_id', 'provider', 'region', 'base_url',
                      'ledger_file', 'total_cap_credits', 'max_attempts', 'allows_upload', 'operations')
            obj(value, fields, fields)
            if (value['schema_version'] != '1.0' or value['approved'] is not True or value['provider'] != PROVIDER
                    or value['region'] != REGION or value['base_url'] != BASE or type(value['allows_upload']) is not bool):
                raise ValueError
            identity(value['approval_id']); canonical_path(value['ledger_file'])
            cap = money_cents(value['total_cap_credits'])
            if not cap:
                raise ValueError
            integer(value['max_attempts'], 1, 10000)
            rows = value['operations']
            if not isinstance(rows, list) or not 1 <= len(rows) <= value['max_attempts']:
                raise ValueError
            seen = set()
            for row in rows:
                obj(row, ('operation_id', 'operation', 'request_sha256', 'max_credits'),
                    ('operation_id', 'operation', 'request_sha256', 'max_credits'))
                op_id = identity(row['operation_id'])
                if op_id in seen or not isinstance(row['operation'], str) or row['operation'] not in OPERATIONS:
                    raise ValueError
                seen.add(op_id); sha(row['request_sha256'])
                if not 0 < money_cents(row['max_credits']) <= cap:
                    raise ValueError
        except Exception:
            raise ProviderError('Invalid explicit overseas authorization') from None

    def operation(self, request, request_digest):
        try:
            self.validate(self.document); sha(self.approval_sha256); sha(request_digest)
            request = normalize_request(request)
            if request_sha256(request) != request_digest:
                raise ValueError
            rows = [row for row in self.document['operations'] if row['operation_id'] == request['operation_id']]
            if len(rows) != 1 or rows[0]['operation'] != request['operation'] or rows[0]['request_sha256'] != request_digest:
                raise ValueError
            return deepcopy(rows[0])
        except Exception:
            raise ProviderError('Request does not match explicit overseas approval') from None


class OverseasLedger:
    def __init__(self, path, authorization=None):
        try:
            self.path = Path(canonical_path(str(path)))
            self.authorization = authorization
            if authorization is not None:
                if not isinstance(authorization, OverseasAuthorization):
                    raise ValueError
                authorization.validate(authorization.document); sha(authorization.approval_sha256)
                if canonical_path(authorization.document['ledger_file']) != canonical_path(str(self.path)):
                    raise ValueError
                self.path.parent.mkdir(parents=True, exist_ok=True)
            with PathLocks([self.path]):
                if self.path.exists():
                    self._read()
                elif authorization is not None:
                    initial = {'schema_version': '1.0', 'provider': PROVIDER, 'region': REGION, 'base_url': BASE,
                               'approval_sha256': authorization.approval_sha256,
                               'approval': deepcopy(authorization.document), 'attempts': [], 'halted': False}
                    self._write(initial)
                else:
                    raise ProviderError('Existing formal overseas ledger required')
        except ProviderError:
            raise
        except Exception:
            raise ProviderError('Overseas ledger unavailable or invalid') from None

    def _validate(self, data):
        try:
            fields = ('schema_version', 'provider', 'region', 'base_url', 'approval_sha256', 'approval', 'attempts', 'halted')
            obj(data, fields, fields)
            if (data['schema_version'] != '1.0' or data['provider'] != PROVIDER or data['region'] != REGION
                    or data['base_url'] != BASE or type(data['halted']) is not bool):
                raise ValueError
            OverseasAuthorization.validate(data['approval']); sha(data['approval_sha256'])
            if canonical_path(data['approval']['ledger_file']) != canonical_path(str(self.path)):
                raise ValueError
            if self.authorization is not None and (data['approval_sha256'] != self.authorization.approval_sha256
                                                   or data['approval'] != self.authorization.document):
                raise ValueError
            rows = data['attempts']; approval = data['approval']
            if not isinstance(rows, list) or len(rows) > approval['max_attempts']:
                raise ValueError
            policies = {row['operation_id']: row for row in approval['operations']}
            ids = set(); op_ids = set(); tasks = set(); unresolved = 0; overrun = False
            for row in rows:
                required = ('id', 'operation_id', 'operation', 'request_sha256', 'reserved_credits', 'state', 'task_id', 'created_at')
                obj(row, required + ('observed_at', 'accounted_credits', 'receipt_sha256'), required)
                row_id = identity(row['id']); op_id = identity(row['operation_id'])
                policy = policies.get(op_id)
                if (row_id in ids or op_id in op_ids or policy is None or row['operation'] != policy['operation']
                        or row['request_sha256'] != policy['request_sha256']
                        or money_cents(row['reserved_credits']) != money_cents(policy['max_credits'])
                        or not isinstance(row['state'], str) or row['state'] not in STATES):
                    raise ValueError
                ids.add(row_id); op_ids.add(op_id)
                for field in ('created_at', 'observed_at'):
                    if field in row:
                        stamp = row[field]
                        if isinstance(stamp, bool) or not isinstance(stamp, (int, float)) or not 0 <= stamp < 1e15:
                            raise ValueError
                if row['task_id'] is not None:
                    task = validate_task_id(row['task_id'])
                    if task in tasks or row['state'] in ('reserved', 'submitting', 'upload_unresolved', 'unknown'):
                        raise ValueError
                    tasks.add(task)
                elif row['state'] not in ('reserved', 'submitting', 'upload_unresolved', 'unknown'):
                    raise ValueError
                if row['state'] not in TERMINAL:
                    unresolved += 1
                if ('accounted_credits' in row) != ('receipt_sha256' in row):
                    raise ValueError
                if 'accounted_credits' in row:
                    if row['state'] not in TERMINAL:
                        raise ValueError
                    sha(row['receipt_sha256'])
                    overrun |= money_cents(row['accounted_credits']) > money_cents(row['reserved_credits'])
            if unresolved > 1:
                raise ValueError
            overrun |= self._worst_cents(data) > money_cents(approval['total_cap_credits'])
            if overrun and not data['halted']:
                raise ValueError
        except Exception:
            raise ProviderError('Invalid formal overseas ledger or approval binding') from None

    @staticmethod
    def _worst_cents(data):
        return sum(money_cents(row.get('accounted_credits', row['reserved_credits'])) for row in data['attempts'])

    def _read(self):
        try:
            data = read_json(self.path)
            self._validate(data)
            return data
        except ProviderError:
            raise
        except Exception:
            raise ProviderError('Overseas ledger unavailable or invalid') from None

    def _write(self, data):
        self._validate(data)
        atomic_json(self.path, data)

    def snapshot(self):
        try:
            with PathLocks([self.path]):
                data = self._read()
                return {'ledger': deepcopy(data), 'worst_or_accounted_credits': self._worst_cents(data) / 100}
        except ProviderError:
            raise
        except Exception:
            raise ProviderError('Overseas ledger snapshot unavailable') from None

    def reserve(self, request, request_digest):
        try:
            if not isinstance(self.authorization, OverseasAuthorization):
                raise ProviderError('Reservation requires explicit overseas authorization')
            request = normalize_request(request)
            policy = self.authorization.operation(request, request_digest)
            with PathLocks([self.path]):
                data = self._read()
                if (data['halted'] or any(row['state'] not in TERMINAL for row in data['attempts'])
                        or any(row['operation_id'] == request['operation_id'] for row in data['attempts'])
                        or len(data['attempts']) >= data['approval']['max_attempts']
                        or self._worst_cents(data) + money_cents(policy['max_credits']) > money_cents(data['approval']['total_cap_credits'])):
                    raise ProviderError('Overseas reservation blocked by existing attempt or approved limit')
                row = {'id': uuid.uuid4().hex, 'operation_id': request['operation_id'], 'operation': request['operation'],
                       'request_sha256': request_digest, 'reserved_credits': money(policy['max_credits']),
                       'state': 'reserved', 'task_id': None, 'created_at': time.time()}
                data['attempts'].append(row); self._write(data)
                return deepcopy(row)
        except ProviderError:
            raise
        except Exception:
            raise ProviderError('Overseas reservation unavailable or invalid') from None

    def _transition(self, attempt_id, source, target, remote_task=None):
        try:
            identity(attempt_id)
            if remote_task is not None:
                remote_task = validate_task_id(remote_task)
            with PathLocks([self.path]):
                data = self._read()
                rows = [row for row in data['attempts'] if row['id'] == attempt_id]
                if len(rows) != 1 or rows[0]['state'] != source:
                    raise ProviderError('Invalid overseas attempt state transition')
                row = rows[0]; row['state'] = target; row['observed_at'] = time.time()
                if remote_task is not None:
                    row['task_id'] = remote_task
                self._write(data)
                return deepcopy(row)
        except ProviderError:
            raise
        except Exception:
            raise ProviderError('Overseas attempt update unavailable or invalid') from None

    def mark_submitting(self, attempt_id):
        return self._transition(attempt_id, 'reserved', 'submitting')

    def mark_upload_unresolved(self, attempt_id):
        return self._transition(attempt_id, 'reserved', 'upload_unresolved')

    def mark_unknown(self, attempt_id):
        return self._transition(attempt_id, 'submitting', 'unknown')

    def confirm(self, attempt_id, task_id):
        return self._transition(attempt_id, 'submitting', 'confirmed', validate_task_id(task_id))

    def observe(self, task_id, state, credits_consumed=None, receipt_sha256=None):
        try:
            remote_task = validate_task_id(task_id)
            if not isinstance(state, str) or state not in ('queued', 'running', *TERMINAL):
                raise ProviderError('Invalid overseas observation state')
            if credits_consumed is not None:
                if state not in TERMINAL or receipt_sha256 is None:
                    raise ProviderError('Overseas accounting requires terminal credits and receipt')
                credits_consumed = money(credits_consumed); sha(receipt_sha256)
            elif receipt_sha256 is not None:
                raise ProviderError('Overseas accounting receipt requires credits')
            with PathLocks([self.path]):
                data = self._read()
                rows = [row for row in data['attempts'] if row['task_id'] == remote_task]
                if len(rows) != 1:
                    raise ProviderError('Observation requires a known task in this overseas ledger')
                row = rows[0]
                if row['state'] in TERMINAL and row['state'] != state:
                    raise ProviderError('Terminal overseas attempt cannot move backward or change outcome')
                if row['state'] == 'running' and state == 'queued':
                    raise ProviderError('Overseas task observation cannot move backward')
                if 'accounted_credits' in row and credits_consumed is not None:
                    if money_cents(row['accounted_credits']) != money_cents(credits_consumed):
                        raise ProviderError('Established overseas accounting cannot be replaced')
                    # A later GET can carry a new observation timestamp. Keep
                    # the original accounting receipt for the same exact cost.
                    receipt_sha256 = row['receipt_sha256']
                row['state'] = state; row['observed_at'] = time.time()
                if credits_consumed is not None:
                    row['accounted_credits'] = credits_consumed; row['receipt_sha256'] = receipt_sha256
                    if money_cents(credits_consumed) > money_cents(row['reserved_credits']):
                        data['halted'] = True
                if self._worst_cents(data) > money_cents(data['approval']['total_cap_credits']):
                    data['halted'] = True
                self._write(data)
                return deepcopy(row)
        except ProviderError:
            raise
        except Exception:
            raise ProviderError('Overseas observation unavailable or invalid') from None
