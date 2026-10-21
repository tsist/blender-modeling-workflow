"""One explicit same-ledger 20-credit/one-attempt increment over frozen unknown.

Only ordinary ledger 1.0 migrates to 1.2; neither legacy nor another increment
can migrate. Local files only. SHA is integrity, never human authentication.
The caller records actual human approval before supplying the authorization.
"""
from contextlib import ExitStack
from copy import deepcopy
from dataclasses import dataclass
import hashlib
from pathlib import Path
import time
import uuid

from ..contract import canonical_path, file_descriptor, identity, normalize_provider_request, obj, request_sha256
from .budget import BudgetLedger, TERMINAL, TrustedAuthorization
from .common import FileGuard, PathLocks, ProviderError, atomic_json, digest
from .approval_append import (_require, _guard_bytes, _identity, _unique, _check_identity,
                              _read, _write_new, _encoded, JSON_LIMIT, EVIDENCE_LIMIT)
from .tripo_cn import RESERVATION_MINIMUM_CREDITS, _png

GENERATION = frozenset(('h31_image', 'h31_multiview', 'p2_multiview'))
CHANGED_FIELDS = ('schema_version', 'approval', 'approval_sha256', 'outstanding_increment')
AUTH_FIELDS = ('schema_version', 'approved', 'kind', 'approval_id', 'source_descriptor',
               'request_descriptor', 'increment_credits', 'increment_attempts',
               'new_operation', 'frozen_attempt_ids')
ROOT_FIELDS = ('schema_version', 'source_sha256', 'authorization_sha256', 'backup_descriptor',
               'authorization_descriptor', 'request_descriptor', 'approval_descriptor',
               'new_operation', 'frozen_attempts')


def _same_descriptor(left, right):
    left, right = file_descriptor(left), file_descriptor(right)
    return (canonical_path(left['file']) == canonical_path(right['file'])
            and left['expected_sha256'] == right['expected_sha256'])


def _source(data, ledger_file):
    _require(data.get('schema_version') == '1.0', 'Only an ordinary schema-1.0 ledger may increment once')
    data = BudgetLedger(ledger_file)._validate(data)
    _require(data['halted'] is False, 'Halted source ledger cannot increment')
    frozen = [row for row in data['attempts'] if row['state'] == 'unknown']
    _require(frozen and all(row['state'] == 'unknown'
             or (row['state'] in TERMINAL and 'accounted_credits' in row) for row in data['attempts']),
             'Source must contain frozen unknown and otherwise terminal accounted history')
    return frozen


def _policy(data, request):
    _require(request['operation'] in GENERATION, 'Increment permits independent standard generation only')
    _require(data['approval']['allows_upload'] is True, 'Increment cannot expand the upload boundary')
    _require(RESERVATION_MINIMUM_CREDITS[request['operation']] <= 20,
             'Operation minimum price exceeds the exact 20-credit increment')
    old_ids = {row['operation_id'] for row in [*data['approval']['operations'], *data['attempts']]}
    _require(request['operation_id'] not in old_ids, 'Increment operation was already approved or consumed')
    return {'operation_id': request['operation_id'], 'operation': request['operation'],
            'request_sha256': request_sha256(request), 'max_credits': 20}


def _authorization(value, source, request_file, data, request, frozen):
    obj(value, AUTH_FIELDS, AUTH_FIELDS)
    _require(value['schema_version'] == '1.0' and value['approved'] is True
             and value['kind'] == 'outstanding_increment', 'Explicit human-approved increment statement required')
    _require(type(value['increment_credits']) is int and value['increment_credits'] == 20
             and type(value['increment_attempts']) is int and value['increment_attempts'] == 1,
             'Increment must be exactly 20 credits and one attempt')
    approval_id = identity(value['approval_id'])
    _require(approval_id != data['approval']['approval_id'], 'Increment requires a new approval identity')
    _require(_same_descriptor(value['source_descriptor'], source)
             and _same_descriptor(value['request_descriptor'], request_file),
             'Increment authorization must bind exact source and request descriptors')
    policy = _policy(data, request)
    _require(value['new_operation'] == policy, 'Increment authorization operation/hash/allowance mismatch')
    _require(value['frozen_attempt_ids'] == [row['id'] for row in frozen],
             'Increment authorization must name every old unknown in order')
    _require(approval_id not in {row['operation_id'] for row in [*data['approval']['operations'], policy]},
             'Approval identity cannot alias an operation')
    approved = deepcopy(data['approval'])
    approved['ledger_file'] = canonical_path(approved['ledger_file'])
    approved.update(approval_id=approval_id, total_cap_credits=approved['total_cap_credits'] + 20,
                    max_attempts=approved['max_attempts'] + 1)
    approved['operations'].append(policy)
    TrustedAuthorization.validate(approved)
    return approved


def _inspect(source_descriptor, request_descriptor):
    source, request_file = file_descriptor(source_descriptor), file_descriptor(request_descriptor)
    ledger_file = canonical_path(source['file'])
    _unique([request_file], ledger_file)
    try:
        with FileGuard(source['file']) as ledger_guard:
            original = _guard_bytes(ledger_guard, source, JSON_LIMIT)
            ledger_identity = _identity(ledger_guard)
            data = BudgetLedger(source['file'])._read()
        frozen = _source(data, ledger_file)
        value, _ = _read(request_file)
        request = normalize_provider_request(value)
        policy = _policy(data, request)
        evidence = _unique([request_file, *[{k: row[k] for k in ('file', 'expected_sha256')}
                                         for row in request['inputs']]], ledger_file)
        with ExitStack() as held:
            seen = {}
            for descriptor in evidence:
                guard = held.enter_context(FileGuard(descriptor['file']))
                _check_identity(guard, descriptor, ledger_identity, seen)
                _guard_bytes(guard, descriptor, EVIDENCE_LIMIT)
            for row in request['inputs']:
                _png(row)
        return {'ledger_file': ledger_file, 'source_sha256': source['expected_sha256'],
                'source_bytes': len(original), 'source_file_identity': list(ledger_identity),
                'ledger': deepcopy(data), 'request': request, 'request_sha256': request_sha256(request),
                'new_operation': policy, 'frozen_attempts': deepcopy(frozen),
                'evidence_descriptors': evidence, 'credits_before': BudgetLedger._total(data),
                'attempts_before': BudgetLedger._attempts_used(data)}, original
    except ProviderError:
        raise
    except Exception:
        raise ProviderError('Outstanding increment source or evidence is invalid') from None


def validate_increment(data, ledger_file):
    """Root-backed history validation for disk reads AND prospective mutations."""
    root = data['outstanding_increment']
    obj(root, ROOT_FIELDS, ROOT_FIELDS)
    _require(root['schema_version'] == '1.0', 'Invalid outstanding increment root version')
    ledger_file = canonical_path(str(ledger_file))
    backup = file_descriptor(root['backup_descriptor'])
    authorization_file = file_descriptor(root['authorization_descriptor'])
    request_file = file_descriptor(root['request_descriptor'])
    approval_file = file_descriptor(root['approval_descriptor'])
    _require(backup['expected_sha256'] == root['source_sha256']
             and authorization_file['expected_sha256'] == root['authorization_sha256'],
             'Outstanding increment source/authorization hashes conflict')
    evidence = _unique([backup, authorization_file, request_file, approval_file], ledger_file)
    _require(len(evidence) == 4, 'Outstanding increment evidence paths must be distinct')
    with ExitStack() as held:
        seen = {}
        # Prevent hard-link aliasing of the mutable ledger even on candidate reads.
        with FileGuard(ledger_file) as ledger_guard:
            ledger_identity = _identity(ledger_guard)
        for descriptor in evidence:
            guard = held.enter_context(FileGuard(descriptor['file']))
            _check_identity(guard, descriptor, ledger_identity, seen)
            _guard_bytes(guard, descriptor, JSON_LIMIT)
        original, _ = _read(backup)
        frozen = _source(original, ledger_file)
        request, _ = _read(request_file)
        request = normalize_provider_request(request)
        statement, _ = _read(authorization_file)
        source = {'file': file_descriptor({'file': ledger_file, 'expected_sha256': root['source_sha256']})['file'],
                  'expected_sha256': root['source_sha256']}
        approved = _authorization(statement, source, request_file, original, request, frozen)
        observed_approval, _ = _read(approval_file)
        observed_approval['ledger_file'] = canonical_path(observed_approval['ledger_file'])
        _require(observed_approval == approved == data['approval']
                 and data['approval_sha256'] == approval_file['expected_sha256'],
                 'Outstanding increment approval differs from its derived immutable document')
    old_rows = original['attempts']
    rows = data['attempts']
    _require(root['new_operation'] == approved['operations'][-1] and root['frozen_attempts'] == frozen,
             'Outstanding increment operation/frozen snapshot changed')
    _require(len(old_rows) <= len(rows) <= len(old_rows) + 1 and rows[:len(old_rows)] == old_rows,
             'Every original attempt must remain an exact ordered prefix')
    extra = rows[len(old_rows):]
    _require(all({key: row[key] for key in ('operation_id', 'operation', 'request_sha256')}
                 == {key: root['new_operation'][key] for key in ('operation_id', 'operation', 'request_sha256')}
                 and row['reserved_credits'] == 20 for row in extra),
             'Only one exact new increment attempt is permitted')
    _require(sum(row['state'] not in TERMINAL for row in rows) <= len(frozen) + 1,
             'Only frozen unknown and one new outstanding attempt are permitted')


@dataclass(frozen=True)
class PreparedOutstandingIncrement:
    source_descriptor: dict
    request_descriptor: dict
    inspection: dict

    def document(self):
        plan = self.inspection
        old = plan['ledger']['approval']
        return {'operation': 'prepare_outstanding_increment', 'status': 'prepared_only',
                'source_descriptor': deepcopy(self.source_descriptor),
                'request_descriptor': deepcopy(self.request_descriptor),
                'ledger_file': plan['ledger_file'], 'ledger_schema_version': '1.0',
                'target_schema_version': '1.2', 'request': deepcopy(plan['request']),
                'request_sha256': plan['request_sha256'], 'new_operation': deepcopy(plan['new_operation']),
                'frozen_attempt_ids': [row['id'] for row in plan['frozen_attempts']],
                'frozen_attempts': deepcopy(plan['frozen_attempts']),
                'preserved_operation_ids': [row['operation_id'] for row in old['operations']],
                'old_approval_sha256': plan['ledger']['approval_sha256'],
                'credits_before': plan['credits_before'], 'attempts_before': plan['attempts_before'],
                'increment_credits': 20, 'increment_attempts': 1,
                'new_total_cap_credits': old['total_cap_credits'] + 20,
                'new_max_attempts': old['max_attempts'] + 1, 'replace_fields': list(CHANGED_FIELDS),
                'backup_plan': {'complete_original_bytes': True, 'bytes': plan['source_bytes'],
                                'expected_sha256': plan['source_sha256'], 'exclusive_create': True},
                'explicit_increment_authorization_required': True,
                'sha_is_integrity_not_human_authentication': True,
                'network_calls': 0, 'source_writes': 0, 'new_financial_attempts': 0}


def prepare(source_descriptor, request_descriptor):
    """Read-only; does not create locks, financial attempts or local artifacts."""
    source, request = file_descriptor(source_descriptor), file_descriptor(request_descriptor)
    inspection, _ = _inspect(source, request)
    return PreparedOutstandingIncrement(source, request, inspection)


def apply(prepared, authorization_descriptor, job_directory, cancel=None):
    """One cooperative CAS with full backup and prepared/applied receipts.

    PathLocks exclude cooperating writers; arbitrary external writers retain the
    same rename-gap limit as approval_append. Ambiguity never restores a backup.
    """
    _require(isinstance(prepared, PreparedOutstandingIncrement), 'Prepared outstanding increment required')
    checkpoint = cancel if cancel is not None else lambda: None
    _require(callable(checkpoint), 'Cancellation checkpoint must be callable')
    checkpoint()
    source, request_file = file_descriptor(prepared.source_descriptor), file_descriptor(prepared.request_descriptor)
    authorization_file = file_descriptor(authorization_descriptor)
    ledger, audit = Path(source['file']), Path(job_directory)
    _require(audit.is_absolute(), 'Increment job directory must be absolute')
    audit = audit.resolve()
    fresh, _ = _inspect(source, request_file)
    _require(fresh == prepared.inspection, 'Prepared increment changed; prepare again')
    evidence = _unique([*fresh['evidence_descriptors'], authorization_file], fresh['ledger_file'])
    _require(canonical_path(authorization_file['file']) not in
             {canonical_path(row['file']) for row in fresh['evidence_descriptors']},
             'Increment authorization must be distinct from request and input evidence')
    _require(canonical_path(str(audit)) not in {fresh['ledger_file'],
             *(canonical_path(row['file']) for row in evidence)}, 'Job directory aliases immutable evidence or ledger')
    statement, _ = _read(authorization_file)
    approved = _authorization(statement, source, request_file, fresh['ledger'], fresh['request'], fresh['frozen_attempts'])
    with PathLocks([ledger, *[row['file'] for row in evidence]]):
        with ExitStack() as held:
            seen = {}
            for descriptor in evidence:
                checkpoint()
                guard = held.enter_context(FileGuard(descriptor['file']))
                _check_identity(guard, descriptor, tuple(fresh['source_file_identity']), seen)
                _guard_bytes(guard, descriptor, EVIDENCE_LIMIT)
            current, original = _inspect(source, request_file)
            _require(current == prepared.inspection, 'Ledger or evidence changed at increment CAS')
            observed, _ = _read(authorization_file)
            _require(_authorization(observed, source, request_file, current['ledger'],
                     current['request'], current['frozen_attempts']) == approved,
                     'Authorization changed at increment CAS')
            checkpoint()
            audit.mkdir(parents=True, exist_ok=True)
            token = uuid.uuid4().hex
            backup_path = audit / ('outstanding-increment-' + token + '.source-bytes')
            _write_new(backup_path, original)
            backup = {'file': str(backup_path.resolve()), 'expected_sha256': source['expected_sha256']}
            approval_path = audit / ('outstanding-increment-' + token + '.approval.json')
            approval_raw = _encoded(approved)
            _write_new(approval_path, approval_raw)
            approval_file = {'file': str(approval_path.resolve()),
                             'expected_sha256': hashlib.sha256(approval_raw).hexdigest()}
            for descriptor in (backup, approval_file):
                guard = held.enter_context(FileGuard(descriptor['file']))
                _guard_bytes(guard, descriptor, JSON_LIMIT)
            updated = deepcopy(current['ledger'])
            updated.update(schema_version='1.2', approval=approved, approval_sha256=approval_file['expected_sha256'],
                outstanding_increment={'schema_version': '1.0', 'source_sha256': source['expected_sha256'],
                    'authorization_sha256': authorization_file['expected_sha256'], 'backup_descriptor': backup,
                    'authorization_descriptor': authorization_file, 'request_descriptor': request_file,
                    'approval_descriptor': approval_file, 'new_operation': deepcopy(current['new_operation']),
                    'frozen_attempts': deepcopy(current['frozen_attempts'])})
            BudgetLedger(ledger)._validate(updated)
            _require(updated['attempts'] == current['ledger']['attempts']
                     and updated['halted'] == current['ledger']['halted'], 'Increment changed financial history or halt')
            after_sha = hashlib.sha256(_encoded(updated)).hexdigest()
            receipt = {'operation': 'apply_outstanding_increment', 'status': 'prepared_for_cas',
                'created_at': time.time(), 'ledger_file': current['ledger_file'],
                'before_sha256': source['expected_sha256'], 'expected_after_sha256': after_sha,
                'backup_descriptor': backup, 'authorization_descriptor': approval_file,
                'increment_authorization_descriptor': authorization_file, 'request_descriptor': request_file,
                'request_sha256': current['request_sha256'], 'new_operation': deepcopy(current['new_operation']),
                'frozen_attempt_ids': [row['id'] for row in current['frozen_attempts']],
                'replace_fields': list(CHANGED_FIELDS), 'credits_before': current['credits_before'],
                'attempts_before': current['attempts_before'], 'network_calls': 0, 'new_financial_attempts': 0,
                'recovery': 'Inspect ledger SHA/state and prepared receipt; never automatically restore backup after ambiguity or later financial changes'}
            receipt_path = audit / ('outstanding-increment-' + token + '.prepared.json')
            _write_new(receipt_path, _encoded(receipt))
            recovery = {**deepcopy(receipt), 'prepared_receipt_file': str(receipt_path.resolve()),
                        'mutation_outcome': 'inspect_current_ledger'}
            try:
                checkpoint()
                _require(digest(ledger) == source['expected_sha256'], 'Ledger changed before increment replacement')
                atomic_json(ledger, updated)
                _require(digest(ledger) == after_sha, 'Ledger changed after increment replacement; preserve current state')
                authorization = TrustedAuthorization.from_descriptor(approval_file)
                snapshot = BudgetLedger(ledger, authorization).snapshot()
                _require(snapshot['ledger'] == updated and snapshot['worst_or_accounted_credits'] == current['credits_before']
                         and snapshot['attempts_used'] == current['attempts_before'], 'Post-increment verification failed')
                receipt.update(status='applied', after_sha256=after_sha,
                               credits_after=snapshot['worst_or_accounted_credits'], attempts_after=snapshot['attempts_used'])
                complete = audit / ('outstanding-increment-' + token + '.applied.json')
                _write_new(complete, _encoded(receipt))
                return {**receipt, 'receipt_file': str(complete.resolve()),
                        'prepared_receipt_file': str(receipt_path.resolve())}
            except BaseException as exc:
                exc.approval_append_recovery = deepcopy(recovery)
                raise
