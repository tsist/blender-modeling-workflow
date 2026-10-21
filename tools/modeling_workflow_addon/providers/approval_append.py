"""Append one caller-approved operation to an existing, fully settled ledger.

prepare is read-only. apply changes only approval/approval_sha256 at the original
ledger path, with exclusive complete-byte backup and prepared/applied receipts.
No credentials, network, attempts, settlement, installation or automatic rollback.
SHA binds bytes; it does not establish whether the human approved an operation.
"""
from contextlib import ExitStack
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import time
import uuid

from ..contract import canonical_path, file_descriptor, normalize_provider_request, request_sha256, sha
from .budget import BudgetLedger, TERMINAL, TrustedAuthorization
from .common import FileGuard, PathLocks, ProviderError, atomic_json, digest, read_json
from .tripo_cn import GENERATION, RESERVATION_MINIMUM_CREDITS, _png
from .legacy_trial import GENERATION as LEGACY_GENERATION

JSON_LIMIT = 2 * 1024 * 1024
EVIDENCE_LIMIT = 64 * 1024 * 1024
CHANGED_FIELDS = ('approval', 'approval_sha256')
APPROVAL_BOUNDARY = ('schema_version', 'approved', 'provider', 'base_url', 'ledger_file',
                     'total_cap_credits', 'max_attempts', 'allows_upload')


def _require(condition, message):
    if not condition:
        raise ProviderError(message)


def _guard_bytes(guard, descriptor, limit):
    blocks = []
    size = 0
    for block in guard.chunks():
        size += len(block)
        _require(size <= limit, 'Approval append evidence exceeds size bound')
        blocks.append(block)
    raw = b''.join(blocks)
    _require(raw and hashlib.sha256(raw).hexdigest() == descriptor['expected_sha256'],
             'Approval append evidence changed')
    return raw


def _identity(guard):
    value = guard.identity()
    return (value['volume'], value['file_index'])


def _unique(descriptors, ledger_file):
    selected = {}
    for value in descriptors:
        descriptor = file_descriptor(value)
        name = canonical_path(descriptor['file'])
        _require(name != ledger_file, 'Immutable evidence aliases mutable ledger')
        prior = selected.get(name)
        _require(prior is None or prior['expected_sha256'] == descriptor['expected_sha256'],
                 'Evidence path has conflicting hashes')
        selected[name] = descriptor
    return [selected[name] for name in sorted(selected)]


def _check_identity(guard, descriptor, ledger_identity, seen):
    name = canonical_path(descriptor['file'])
    value = _identity(guard)
    _require(value != ledger_identity, 'Immutable evidence is a hard-link alias of mutable ledger')
    _require(value not in seen or seen[value] == name, 'Evidence contains hard-link aliases')
    seen[value] = name


def _read(descriptor):
    descriptor = file_descriptor(descriptor)
    try:
        with FileGuard(descriptor['file']) as guard:
            raw = _guard_bytes(guard, descriptor, JSON_LIMIT)
            # The shared reader rejects duplicate keys and reads this guarded generation.
            return read_json(descriptor['file']), raw
    except ProviderError:
        raise
    except Exception:
        raise ProviderError('Approval append JSON evidence unavailable or invalid') from None


def _parent(data, request):
    if request['operation'] in GENERATION:
        return None
    # Matches BudgetLedger.known_task: only current attempts, never legacy_history.
    # Do not call snapshot/known_task here: prepare must not acquire PathLocks.
    found = [row for row in data['attempts'] if row['task_id'] == request['source_task_id']]
    _require(len(found) == 1, 'Parent task is not recorded as a current attempt in this ledger')
    row = found[0]
    allowed = {'segment_v2'} if request['operation'] == 'complete_all_v1' else GENERATION
    _require(row['state'] == 'succeeded' and row['operation'] in allowed,
             'Postprocessing requires a successful supported parent operation in this ledger')
    return {'task_id': row['task_id'], 'operation': row['operation'], 'attempt_id': row['id'],
            'request_sha256': row['request_sha256']}


def _inspect(source_descriptor, request_descriptor):
    source = file_descriptor(source_descriptor)
    request_file = file_descriptor(request_descriptor)
    ledger_file = canonical_path(source['file'])
    _unique([request_file], ledger_file)
    try:
        with FileGuard(source['file']) as ledger_guard:
            original = _guard_bytes(ledger_guard, source, JSON_LIMIT)
            ledger_identity = _identity(ledger_guard)
            data = BudgetLedger(source['file'])._read()
        _require(data['halted'] is False, 'Halted ledger cannot append approval')
        rows = [*data.get('legacy_history', []), *data['attempts']]
        _require(all(row['state'] in TERMINAL and 'accounted_credits' in row for row in rows),
                 'Every attempt must be terminal and accounted before appending approval')
        approved = data['approval']
        total = BudgetLedger._total(data)
        used = BudgetLedger._attempts_used(data)
        remaining_credits = approved['total_cap_credits'] - total
        remaining_attempts = approved['max_attempts'] - used
        _require(remaining_credits > 0 and remaining_attempts > 0,
                 'No cumulative budget or attempt allowance remains')
        _require(len(approved['operations']) < approved['max_attempts'],
                 'No approval-list capacity remains within the unchanged attempt cap')
        request_value, _ = _read(request_file)
        request = normalize_provider_request(request_value)
        # The existing schema-1.1 history validator still limits approvals to generation.
        # Reject before write instead of producing a ledger its owner cannot read.
        _require(data['schema_version'] != '1.1' or request['operation'] in LEGACY_GENERATION,
                 'Existing schema-1.1 history contract only permits independent generation approvals')
        prior_ids = {row['operation_id'] for row in [*approved['operations'], *rows]}
        _require(request['operation_id'] not in prior_ids, 'Operation identity is already approved or consumed')
        _require(remaining_credits >= RESERVATION_MINIMUM_CREDITS[request['operation']],
                 'Remaining budget is below the operation minimum price')
        parent = _parent(data, request)
        if request['operation'] in GENERATION:
            _require(approved['allows_upload'] is True, 'Image upload is outside the unchanged approval boundary')
        evidence = [request_file]
        if data['schema_version'] == '1.1':
            from .legacy_trial import _inspect as inspect_legacy
            root = data['legacy_root']
            legacy, _ = inspect_legacy(root['backup_descriptor'], root['review_descriptor'], ledger_file)
            evidence += [root['backup_descriptor'], *legacy['evidence_descriptors']]
        image_inputs = [{'file': row['file'], 'expected_sha256': row['expected_sha256']}
                        for row in request.get('inputs', [])]
        evidence = _unique([*evidence, *image_inputs], ledger_file)
        seen = {}
        with ExitStack() as held:
            for descriptor in evidence:
                guard = held.enter_context(FileGuard(descriptor['file']))
                _check_identity(guard, descriptor, ledger_identity, seen)
                _guard_bytes(guard, descriptor, EVIDENCE_LIMIT)
            for row in request.get('inputs', []):
                _png(row)
        plan = {'ledger_file': ledger_file, 'source_sha256': source['expected_sha256'],
                'source_bytes': len(original), 'source_file_identity': list(ledger_identity),
                'ledger': deepcopy(data), 'request': request, 'request_sha256': request_sha256(request),
                'parent_task': parent, 'evidence_descriptors': evidence,
                'credits_before': total, 'attempts_before': used,
                'remaining_credits': remaining_credits, 'remaining_attempts': remaining_attempts,
                'minimum_operation_credits': RESERVATION_MINIMUM_CREDITS[request['operation']]}
        return plan, original
    except ProviderError:
        raise
    except Exception:
        raise ProviderError('Approval append source or evidence is unsupported or incomplete') from None


@dataclass(frozen=True)
class PreparedApprovalAppend:
    source_descriptor: dict
    request_descriptor: dict
    inspection: dict

    def document(self):
        plan = self.inspection
        old = plan['ledger']['approval']
        return {'operation': 'prepare_approval_append', 'status': 'prepared_only',
                'source_descriptor': deepcopy(self.source_descriptor),
                'request_descriptor': deepcopy(self.request_descriptor),
                'request': deepcopy(plan['request']), 'request_sha256': plan['request_sha256'],
                'parent_task': deepcopy(plan['parent_task']), 'ledger_file': plan['ledger_file'],
                'ledger_schema_version': plan['ledger']['schema_version'],
                'credits_before': plan['credits_before'], 'attempts_before': plan['attempts_before'],
                'remaining_credits': plan['remaining_credits'], 'remaining_attempts': plan['remaining_attempts'],
                'unchanged_cap_credits': old['total_cap_credits'], 'unchanged_attempt_cap': old['max_attempts'],
                'minimum_operation_credits': plan['minimum_operation_credits'],
                'old_approval_sha256': plan['ledger']['approval_sha256'],
                'preserved_operation_ids': [row['operation_id'] for row in old['operations']],
                'replace_fields': list(CHANGED_FIELDS),
                'backup_plan': {'complete_original_bytes': True, 'bytes': plan['source_bytes'],
                                'expected_sha256': plan['source_sha256'], 'exclusive_create': True,
                                'location': 'Caller-specified absolute audit directory; unique .source-bytes'},
                'trusted_authorization_and_descriptor_required': True,
                'sha_is_integrity_not_human_authentication': True,
                'network_calls': 0, 'source_writes': 0, 'new_financial_attempts': 0}


def prepare(source_descriptor, request_descriptor):
    """No ledger/approval/receipt/lock writes, scheduling or network calls."""
    source = file_descriptor(source_descriptor)
    request = file_descriptor(request_descriptor)
    inspection, _ = _inspect(source, request)
    return PreparedApprovalAppend(source, request, inspection)


def _approval(prepared, authorization, descriptor):
    _require(isinstance(authorization, TrustedAuthorization), 'Caller-trusted authorization is required')
    descriptor = file_descriptor(descriptor)
    observed, _ = _read(descriptor)
    TrustedAuthorization.validate(observed)
    observed['ledger_file'] = canonical_path(observed['ledger_file'])
    _require(observed == authorization.document
             and descriptor['expected_sha256'] == sha(authorization.approval_sha256),
             'Trusted authorization differs from its immutable descriptor')
    old = prepared.inspection['ledger']['approval']
    for field in APPROVAL_BOUNDARY:
        _require(observed[field] == old[field], 'Approval append changes the existing approval boundary')
    _require(observed['approval_id'] != old['approval_id'], 'New approval identity must differ from previous approval')
    prefix = old['operations']
    policies = observed['operations']
    _require(len(policies) == len(prefix) + 1 and policies[:-1] == prefix,
             'Approval append must preserve every old operation as an exact ordered prefix')
    policy = policies[-1]
    request = prepared.inspection['request']
    _require(policy['operation_id'] == request['operation_id'] and policy['operation'] == request['operation']
             and policy['request_sha256'] == prepared.inspection['request_sha256'],
             'Added operation must match the exact prepared request')
    _require(policy['operation_id'] != observed['approval_id'], 'Operation and approval identities must be distinct')
    _require(prepared.inspection['minimum_operation_credits'] <= policy['max_credits']
             <= prepared.inspection['remaining_credits'],
             'New allowance must cover the operation minimum price within remaining cumulative budget')
    return deepcopy(observed)


def _write_new(path, raw):
    with Path(path).open('xb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def _encoded(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf-8')


def apply(prepared, authorization, authorization_descriptor, audit_directory):
    """Cooperative-path CAS, full backup, receipts; never automatically rollback.

    Immutable evidence stays FileGuard-held during replacement. The ledger guard
    only exists during reads and is released before atomic_json replaces it.
    PathLocks exclude cooperating writers, not arbitrary non-cooperating writers;
    this is not an OS atomic compare-and-swap across a hostile writer gap.
    """
    _require(isinstance(prepared, PreparedApprovalAppend), 'Prepared approval append evidence is required')
    source = file_descriptor(prepared.source_descriptor)
    request = file_descriptor(prepared.request_descriptor)
    approval_file = file_descriptor(authorization_descriptor)
    ledger = Path(source['file'])
    audit = Path(audit_directory)
    _require(audit.is_absolute(), 'Audit directory must be absolute')
    audit = audit.resolve()
    _require(canonical_path(approval_file['file']) != canonical_path(request['file']),
             'Approval and request evidence paths must be distinct')
    fresh, _ = _inspect(source, request)
    _require(fresh == prepared.inspection, 'Prepared append changed; prepare the current ledger again')
    evidence = _unique([*fresh['evidence_descriptors'], approval_file], fresh['ledger_file'])
    _require(canonical_path(str(audit)) not in {fresh['ledger_file'],
             *(canonical_path(row['file']) for row in evidence)}, 'Audit directory aliases ledger or immutable evidence')
    approved = _approval(prepared, authorization, approval_file)
    with PathLocks([ledger, *[row['file'] for row in evidence]]):
        with ExitStack() as held:
            seen = {}
            for descriptor in evidence:
                guard = held.enter_context(FileGuard(descriptor['file']))
                _check_identity(guard, descriptor, tuple(fresh['source_file_identity']), seen)
                _guard_bytes(guard, descriptor, EVIDENCE_LIMIT)
            current, original = _inspect(source, request)
            _require(current == prepared.inspection, 'Ledger or evidence changed at approval append CAS')
            _require(_approval(prepared, authorization, approval_file) == approved,
                     'Approval changed at approval append CAS')
            updated = deepcopy(current['ledger'])
            updated['approval'] = approved
            updated['approval_sha256'] = approval_file['expected_sha256']
            _require({k: v for k, v in updated.items() if k not in CHANGED_FIELDS}
                     == {k: v for k, v in current['ledger'].items() if k not in CHANGED_FIELDS},
                     'Approval append changed non-approval financial fields')
            audit.mkdir(parents=True, exist_ok=True)
            token = uuid.uuid4().hex
            backup_path = audit / ('approval-append-' + token + '.source-bytes')
            _write_new(backup_path, original)
            backup = {'file': str(backup_path.resolve()), 'expected_sha256': source['expected_sha256']}
            guard = held.enter_context(FileGuard(backup_path))
            _guard_bytes(guard, backup, JSON_LIMIT)
            after_sha = hashlib.sha256(_encoded(updated)).hexdigest()
            receipt = {'operation': 'apply_approval_append', 'status': 'prepared_for_cas',
                       'created_at': time.time(), 'ledger_file': current['ledger_file'],
                       'before_sha256': source['expected_sha256'], 'expected_after_sha256': after_sha,
                       'backup_descriptor': backup, 'approval_descriptor': approval_file,
                       'request_descriptor': request, 'request_sha256': current['request_sha256'],
                       'parent_task': deepcopy(current['parent_task']),
                       'new_operation': deepcopy(approved['operations'][-1]), 'replace_fields': list(CHANGED_FIELDS),
                       'credits_before': current['credits_before'], 'attempts_before': current['attempts_before'],
                       'network_calls': 0, 'new_financial_attempts': 0,
                       'recovery': 'Inspect current ledger SHA/state and prepared receipt after ambiguity; never automatically restore backup after any later attempts or accounting'}
            receipt_path = audit / ('approval-append-' + token + '.prepared.json')
            _write_new(receipt_path, _encoded(receipt))
            recovery = {'ledger_file': current['ledger_file'], 'before_sha256': source['expected_sha256'],
                        'expected_after_sha256': after_sha, 'backup_descriptor': backup,
                        'prepared_receipt_file': str(receipt_path.resolve()),
                        'credits_before': current['credits_before'], 'attempts_before': current['attempts_before'],
                        'network_calls': 0, 'new_financial_attempts': 0,
                        'mutation_outcome': 'inspect_current_ledger',
                        'recovery': receipt['recovery']}
            try:
                _require(digest(ledger) == source['expected_sha256'], 'Ledger changed before atomic approval replacement')
                atomic_json(ledger, updated)
                _require(digest(ledger) == after_sha, 'Ledger changed after replacement; preserve current financial state')
                snapshot = BudgetLedger(ledger, authorization).snapshot()
                _require(snapshot['ledger'] == updated
                         and snapshot['worst_or_accounted_credits'] == current['credits_before']
                         and snapshot['attempts_used'] == current['attempts_before'],
                         'Post-append verification failed; preserve current financial state')
                receipt.update(status='applied', after_sha256=after_sha,
                               credits_after=snapshot['worst_or_accounted_credits'], attempts_after=snapshot['attempts_used'])
                complete = audit / ('approval-append-' + token + '.applied.json')
                _write_new(complete, _encoded(receipt))
                return {**receipt, 'receipt_file': str(complete.resolve()),
                        'prepared_receipt_file': str(receipt_path.resolve())}
            except BaseException as exc:
                # Do not roll back or infer the outcome from an exception. The
                # durable prepared receipt binds the full backup and expected
                # generations even when an applied receipt cannot be written.
                exc.approval_append_recovery = deepcopy(recovery)
                raise
