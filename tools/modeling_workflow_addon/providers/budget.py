"""Financial reservation state. No scheduling, retries, credentials or URLs."""
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import math
from pathlib import Path
import time
import uuid

from ..contract import (obj, identity, integer, sha, PROVIDER_OPERATIONS, file_descriptor, canonical_path,
                        normalize_binding_document, normalize_provider_request, request_sha256)
from .common import ProviderError, PathLocks, FileGuard, atomic_json, digest, read_json

TERMINAL = {'succeeded', 'failed', 'cancelled'}
STATES = {'reserved', 'submitting', 'unknown', 'confirmed', 'queued', 'running', *TERMINAL}


def _guarded_descriptor(descriptor, max_bytes, read_document=False):
    """Verify immutable local evidence without returning its arbitrary contents."""
    descriptor = file_descriptor(descriptor)
    try:
        with FileGuard(descriptor['file']) as guard:
            checksum = hashlib.sha256(); size = 0
            for block in guard.chunks():
                size += len(block)
                if size > max_bytes:
                    raise ProviderError('Binding document/evidence exceeds size bound')
                checksum.update(block)
            if not size or checksum.hexdigest() != descriptor['expected_sha256']:
                raise ProviderError('Binding document/evidence changed')
            # FileGuard denies write/delete for this same file generation.
            return read_json(descriptor['file']) if read_document else descriptor
    except ProviderError:
        raise
    except Exception:
        raise ProviderError('Binding document/evidence unavailable or invalid') from None


@dataclass(frozen=True)
class TrustedBinding:
    """Operator-verified mapping, not an automatic inference or identity proof."""
    document: dict
    binding_sha256: str
    descriptor: dict

    @classmethod
    def from_descriptor(cls, descriptor):
        descriptor = file_descriptor(descriptor)
        document = normalize_binding_document(_guarded_descriptor(descriptor, 256 * 1024, True))
        _guarded_descriptor(document['verification']['evidence_descriptor'], 32 * 1024 * 1024)
        return cls(document, descriptor['expected_sha256'], descriptor)

    def verify(self):
        current = self.from_descriptor(self.descriptor)
        if current.document != self.document or current.binding_sha256 != self.binding_sha256:
            raise ProviderError('Binding statement changed after operator review')
        return current.document


@dataclass(frozen=True)
class TrustedAuthorization:
    """Caller declaration of prior human approval, never inferred from existence.

    The caller must obtain approval before writing the descriptor. The SHA binds
    its contents; it is integrity evidence, not authentication of the human.
    """
    document: dict
    approval_sha256: str

    @classmethod
    def from_descriptor(cls, descriptor):
        descriptor = file_descriptor(descriptor)
        location = Path(descriptor['file'])
        with PathLocks([location]):
            if digest(location) != descriptor['expected_sha256']:
                raise ProviderError('Approval file changed')
            document = read_json(location)
        cls.validate(document)
        document['ledger_file'] = canonical_path(document['ledger_file'])
        return cls(deepcopy(document), descriptor['expected_sha256'])

    @staticmethod
    def validate(value):
        obj(value, ('schema_version', 'approved', 'approval_id', 'provider', 'base_url',
                    'ledger_file', 'total_cap_credits', 'max_attempts', 'allows_upload', 'operations'),
            ('schema_version', 'approved', 'approval_id', 'provider', 'base_url',
             'ledger_file', 'total_cap_credits', 'max_attempts', 'allows_upload', 'operations'))
        if (value['schema_version'] != '1.0' or value['approved'] is not True
                or value['provider'] != 'tripo-cn' or value['base_url'] != 'https://openapi.tripo3d.com/v3'
                or type(value['allows_upload']) is not bool):
            raise ProviderError('Explicit China provider approval required')
        identity(value['approval_id'])
        canonical_path(value['ledger_file'])
        integer(value['total_cap_credits'], 1, 1000000000)
        integer(value['max_attempts'], 1, 10000)
        rows = value['operations']
        if not isinstance(rows, list) or not 1 <= len(rows) <= value['max_attempts']:
            raise ProviderError('Invalid approved operation list')
        ids = set()
        for row in rows:
            obj(row, ('operation_id', 'operation', 'request_sha256', 'max_credits'),
                ('operation_id', 'operation', 'request_sha256', 'max_credits'))
            op_id = identity(row['operation_id'])
            if op_id in ids or row['operation'] not in PROVIDER_OPERATIONS:
                raise ProviderError('Repeated or unsupported approved operation')
            ids.add(op_id)
            sha(row['request_sha256'])
            integer(row['max_credits'], 1, value['total_cap_credits'])

    def operation(self, request, request_digest):
        self.validate(self.document)
        sha(self.approval_sha256)
        found = [r for r in self.document['operations'] if r['operation_id'] == request['operation_id']]
        if len(found) != 1 or found[0]['operation'] != request['operation'] or found[0]['request_sha256'] != request_digest:
            raise ProviderError('Request does not match approved operation/hash')
        return deepcopy(found[0])


class BudgetLedger:
    def __init__(self, path, authorization=None):
        self.path = Path(path)
        if not self.path.is_absolute():
            raise ProviderError('Ledger must have an absolute path')
        self.path = self.path.resolve()
        self.authorization = authorization
        if authorization is not None:
            if not isinstance(authorization, TrustedAuthorization):
                raise ProviderError('Caller must provide trusted authorization')
            authorization.validate(authorization.document)
            if canonical_path(str(self.path)) != canonical_path(authorization.document['ledger_file']):
                raise ProviderError('Requested path does not match the approval bound ledger')
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with PathLocks([self.path]):
                if not self.path.exists():
                    atomic_json(self.path, {'schema_version': '1.0', 'provider': 'tripo-cn',
                        'approval_sha256': sha(authorization.approval_sha256),
                        'approval': deepcopy(authorization.document), 'attempts': [], 'halted': False})
                self._read()

    def _read(self):
        return self._validate(read_json(self.path))

    def _validate(self, data):
        """Validate the supplied generation, including candidates before writes."""
        fields = ('schema_version', 'provider', 'approval_sha256', 'approval', 'attempts', 'halted')
        if data.get('schema_version') == '1.1':
            fields += ('legacy_root', 'legacy_history')
        elif data.get('schema_version') == '1.2':
            fields += ('outstanding_increment',)
        obj(data, fields, fields)
        if data['schema_version'] not in ('1.0', '1.1', '1.2') or data['provider'] != 'tripo-cn' or type(data['halted']) is not bool:
            raise ProviderError('Invalid ledger identity')
        sha(data['approval_sha256'])
        TrustedAuthorization.validate(data['approval'])
        if canonical_path(str(self.path)) != canonical_path(data['approval']['ledger_file']):
            raise ProviderError('Loaded path does not match the approval bound ledger')
        if self.authorization and (data['approval_sha256'] != self.authorization.approval_sha256
                                   or data['approval'] != self.authorization.document):
            raise ProviderError('Ledger belongs to a different approval')
        history = []
        if data['schema_version'] == '1.1':
            from .legacy_trial import GENERATION as LEGACY_GENERATION, validate_history
            # Keep the migration approval scope independent of provider growth.
            if any(row['operation'] not in LEGACY_GENERATION for row in data['approval']['operations']):
                raise ProviderError('Schema-1.1 history only permits independent legacy generation approvals')
            history = validate_history(data, self.path)
        rows = data['attempts']
        if not isinstance(rows, list) or len(rows) + len(history) > data['approval']['max_attempts']:
            raise ProviderError('Invalid ledger attempt count')
        ids = {r['id'] for r in history}
        operation_ids = {r['operation_id'] for r in history}
        task_ids = {r['task_id'] for r in history}
        approved = {r['operation_id']: r for r in data['approval']['operations']}
        for row in rows:
            obj(row, ('id', 'operation_id', 'operation', 'request_sha256', 'reserved_credits',
                      'state', 'task_id', 'created_at', 'observed_at', 'accounted_credits', 'receipt_sha256', 'binding_receipt',
                      'pre_submit_recovery_receipt'),
                ('id', 'operation_id', 'operation', 'request_sha256', 'reserved_credits', 'state', 'task_id', 'created_at'))
            row_id = identity(row['id']); op_id = identity(row['operation_id'])
            policy = approved.get(op_id)
            if (row_id in ids or op_id in operation_ids or policy is None
                    or row['operation'] != policy['operation'] or row['request_sha256'] != policy['request_sha256']
                    or row['reserved_credits'] != policy['max_credits'] or row['state'] not in STATES):
                raise ProviderError('Ledger attempt conflicts with approval')
            ids.add(row_id); operation_ids.add(op_id)
            if row['task_id'] is not None:
                task = identity(row['task_id'])
                if task in task_ids:
                    raise ProviderError('Duplicate task identity')
                task_ids.add(task)
            if 'accounted_credits' in row:
                integer(row['accounted_credits'], 0, 1000000000); sha(row.get('receipt_sha256'))
                if row['state'] not in TERMINAL:
                    raise ProviderError('Unresolved reservation was released')
            if 'binding_receipt' in row:
                receipt = row['binding_receipt']
                fields = ('binding_sha256', 'evidence_sha256', 'remote_snapshot_sha256',
                          'request_sha256', 'verified_request_match', 'bound_at')
                obj(receipt, fields, fields)
                for key in ('binding_sha256', 'evidence_sha256', 'remote_snapshot_sha256', 'request_sha256'):
                    sha(receipt[key])
                if (receipt['verified_request_match'] is not True or receipt['request_sha256'] != row['request_sha256']
                        or row['task_id'] is None or row['state'] in ('reserved', 'submitting', 'unknown')
                        or type(receipt['bound_at']) not in (int, float) or not math.isfinite(receipt['bound_at']) or receipt['bound_at'] < 0):
                    raise ProviderError('Invalid binding receipt identity/state')
            if 'pre_submit_recovery_receipt' in row:
                receipt = row['pre_submit_recovery_receipt']
                fields = ('recovery_sha256', 'evidence_sha256', 'prior_implementation_sha256',
                          'request_sha256', 'claim_nonce', 'claimed_at')
                obj(receipt, fields, fields)
                for key in ('recovery_sha256', 'evidence_sha256', 'prior_implementation_sha256', 'request_sha256'):
                    sha(receipt[key])
                nonce = identity(receipt['claim_nonce'])
                if (len(nonce) != 32 or any(c not in '0123456789abcdef' for c in nonce)
                        or receipt['request_sha256'] != row['request_sha256'] or row['state'] == 'reserved'
                        or row['operation'] not in ('h31_image', 'h31_multiview', 'p2_multiview', 'h31_parts_multiview')
                        or type(receipt['claimed_at']) not in (int, float)
                        or not math.isfinite(receipt['claimed_at']) or receipt['claimed_at'] < 0):
                    raise ProviderError('Invalid pre-submit recovery receipt identity/state')
        if data['schema_version'] == '1.2':
            from .outstanding_increment import validate_increment
            validate_increment(data, self.path)
        elif sum(r['state'] not in TERMINAL for r in rows) > 1:
            raise ProviderError('Concurrent outstanding billing is unsupported')
        if self._total(data) > data['approval']['total_cap_credits'] and data['halted'] is not True:
            raise ProviderError('Over-cap ledger must retain its halted state')
        return data

    @staticmethod
    def _total(data):
        return sum(r.get('accounted_credits', r['reserved_credits'])
                   for r in [*data.get('legacy_history', []), *data['attempts']])

    @staticmethod
    def _attempts_used(data):
        return len(data.get('legacy_history', [])) + len(data['attempts'])

    def _mutate(self, change):
        with PathLocks([self.path]):
            data = self._read()
            prior = deepcopy(data)
            result = change(data)
            # Validate THIS candidate, never re-read the old disk generation.
            # Schema 1.0/1.1 retain their historical mutation semantics.
            if prior['schema_version'] == '1.2':
                if (data.get('schema_version') != '1.2'
                        or data.get('outstanding_increment') != prior['outstanding_increment']
                        or data.get('approval') != prior['approval']
                        or data.get('approval_sha256') != prior['approval_sha256']
                        or (prior['halted'] and data.get('halted') is not True)):
                    raise ProviderError('Outstanding increment identity/history/halt cannot change')
                self._validate(data)
            atomic_json(self.path, data)
            return deepcopy(result)

    def snapshot(self):
        with PathLocks([self.path]):
            data = self._read()
            return {'ledger': deepcopy(data), 'worst_or_accounted_credits': self._total(data),
                    'attempts_used': self._attempts_used(data)}

    def reserve(self, request, request_digest):
        if self.authorization is None:
            raise ProviderError('Submission requires trusted authorization')
        policy = self.authorization.operation(request, request_digest)
        if request['operation'] == 'h31_parts_multiview':
            if request_sha256(request) != request_digest:
                raise ProviderError('Native parts reservation request hash mismatch')
            if policy['max_credits'] < 40:
                raise ProviderError('Native parts reservation is below documented minimum price')
        def change(data):
            outstanding = [r for r in data['attempts'] if r['state'] not in TERMINAL]
            if data['schema_version'] == '1.2':
                increment = data['outstanding_increment']
                exact = increment['new_operation']
                if (policy != exact or request_sha256(request) != request_digest
                        or outstanding != increment['frozen_attempts']):
                    raise ProviderError('Only the exact unused outstanding increment operation may reserve')
            elif outstanding:
                raise ProviderError('Outstanding/unknown reservation; recover instead of resubmitting')
            if data['halted']:
                raise ProviderError('Outstanding/unknown reservation; recover instead of resubmitting')
            all_rows = [*data.get('legacy_history', []), *data['attempts']]
            if self._attempts_used(data) >= data['approval']['max_attempts'] or any(r['operation_id'] == request['operation_id'] for r in all_rows):
                raise ProviderError('Approved operation/attempt already consumed')
            if self._total(data) + policy['max_credits'] > data['approval']['total_cap_credits']:
                raise ProviderError('Budget cap exceeded')
            row = {'id': uuid.uuid4().hex, 'operation_id': request['operation_id'], 'operation': request['operation'],
                   'request_sha256': request_digest, 'reserved_credits': policy['max_credits'],
                   'state': 'reserved', 'task_id': None, 'created_at': time.time()}
            if any(other['id'] == row['id'] for other in all_rows):
                raise ProviderError('Generated attempt identity conflicts with history')
            data['attempts'].append(row)
            return row
        return self._mutate(change)

    def known_task(self, task_id):
        identity(task_id)
        data = self.snapshot()['ledger']
        found = [r for r in data['attempts'] if r['task_id'] == task_id]
        if len(found) != 1:
            raise ProviderError('Task is not recorded in this ledger')
        return deepcopy(found[0])

    def _binding_row(self, data, request, binding):
        if not isinstance(binding, TrustedBinding):
            raise ProviderError('Operator-verified binding statement required')
        statement = normalize_binding_document(binding.document)
        request = normalize_provider_request(request)
        if (canonical_path(str(self.path)) != statement['ledger_file']
                or data['approval_sha256'] != statement['approval_sha256']
                or digest(self.path) != statement['expected_ledger_sha256']):
            raise ProviderError('Binding ledger/approval/snapshot mismatch')
        found = [row for row in data['attempts'] if row['id'] == statement['attempt_id']]
        if len(found) != 1:
            raise ProviderError('Binding attempt identity mismatch')
        row = found[0]
        if (data['schema_version'] == '1.2'
                and row['id'] in {r['id'] for r in data['outstanding_increment']['frozen_attempts']}):
            raise ProviderError('Frozen unknown cannot bind, recover, settle or change')
        approved = [policy for policy in data['approval']['operations'] if policy['operation_id'] == statement['operation_id']]
        if len(approved) != 1:
            raise ProviderError('Binding operation is outside original approval')
        policy = approved[0]
        request_hash = request_sha256(request)
        if (row['state'] != 'unknown' or row['task_id'] is not None
                or any(other['task_id'] == statement['task_id']
                       for other in [*data.get('legacy_history', []), *data['attempts']])
                or any(request['operation_id'] != source['operation_id'] or request['operation'] != source['operation']
                       or request_hash != source['request_sha256'] for source in (row, policy, statement))
                or row['reserved_credits'] != policy['max_credits']):
            raise ProviderError('Binding request/operation/state/task conflicts with original reservation')
        return row

    def binding_snapshot(self, request, binding):
        """Local preflight; never creates a ledger or performs a remote call."""
        if not self.path.is_file():
            raise ProviderError('Binding requires the existing approval-bound ledger')
        if not isinstance(binding, TrustedBinding):
            raise ProviderError('Operator-verified binding statement required')
        binding.verify()
        with PathLocks([self.path]):
            data = self._read()
            return deepcopy(self._binding_row(data, request, binding))

    def bind_unknown(self, request, binding, task_id, remote_snapshot_sha256):
        """CAS mapping only. Preserve every reservation, attempt and charge field."""
        if not isinstance(binding, TrustedBinding):
            raise ProviderError('Operator-verified binding statement required')
        binding.verify()
        if identity(task_id) != binding.document['task_id']:
            raise ProviderError('Remote task does not match reviewed binding')
        sha(remote_snapshot_sha256)
        def change(data):
            row = self._binding_row(data, request, binding)
            row.update(state='confirmed', task_id=task_id, observed_at=time.time(), binding_receipt={
                'binding_sha256': sha(binding.binding_sha256),
                'evidence_sha256': binding.document['verification']['evidence_descriptor']['expected_sha256'],
                'remote_snapshot_sha256': remote_snapshot_sha256, 'request_sha256': row['request_sha256'],
                'verified_request_match': True, 'bound_at': time.time()})
            return row
        return self._mutate(change)

    def update(self, attempt_id, state, task_id=None, accounted_credits=None, receipt_sha256=None):
        if state not in STATES - {'reserved'}:
            raise ProviderError('Invalid ledger transition')
        def change(data):
            found = [r for r in data['attempts'] if r['id'] == attempt_id]
            if len(found) != 1:
                raise ProviderError('Unknown ledger attempt')
            row = found[0]; prior = row['state']
            if prior in TERMINAL and prior != state:
                raise ProviderError('Terminal state cannot change')
            if state == 'submitting' and prior != 'reserved':
                raise ProviderError('Submission cannot be retried')
            if state == 'unknown' and prior != 'submitting':
                raise ProviderError('Unknown requires a submitted attempt')
            if state == 'confirmed' and prior == 'unknown':
                raise ProviderError('Unknown task confirmation requires reviewed bind_unknown evidence')
            if task_id is not None:
                identity(task_id)
                if state != 'confirmed' or prior != 'submitting' or row['task_id'] not in (None, task_id):
                    raise ProviderError('Task confirmation cannot change identity')
                if any(other['id'] != row['id'] and other['task_id'] == task_id
                       for other in [*data.get('legacy_history', []), *data['attempts']]):
                    raise ProviderError('Provider task identity is already bound to another attempt')
                row['task_id'] = task_id
            if state not in ('submitting', 'unknown') and row['task_id'] is None:
                raise ProviderError('Remote observation requires a known task')
            row.update(state=state, observed_at=time.time())
            if accounted_credits is not None:
                if state not in TERMINAL:
                    raise ProviderError('Pending task cannot release reservation')
                integer(accounted_credits, 0, 1000000000); sha(receipt_sha256)
                row.update(accounted_credits=accounted_credits, receipt_sha256=receipt_sha256)
                if accounted_credits > row['reserved_credits'] or self._total(data) > data['approval']['total_cap_credits']:
                    data['halted'] = True
            return row
        return self._mutate(change)
