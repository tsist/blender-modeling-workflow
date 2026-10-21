"""Extend one settled schema-1.1 ledger with one separately approved generation.

prepare is read-only. apply needs caller-obtained TrustedAuthorization AND its
immutable descriptor; it does not create approval, upload, submit, settle, reset
history, install, or automatically restore a financial backup. Not a CLI entry.
SHA integrity is not authentication of the human who approved the operation.
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
from .legacy_trial import GENERATION, SLOTS, _inspect as inspect_legacy

JSON_LIMIT = 2 * 1024 * 1024
INPUT_LIMIT = 64 * 1024 * 1024
CHANGED_FIELDS = ('approval', 'approval_sha256')


def _require(condition, message):
    if not condition:
        raise ProviderError(message)


def _guard_bytes(guard, descriptor, limit):
    blocks = []; size = 0
    for block in guard.chunks():
        size += len(block)
        _require(size <= limit, 'Extension evidence exceeds size bound')
        blocks.append(block)
    raw = b''.join(blocks)
    _require(raw and hashlib.sha256(raw).hexdigest() == descriptor['expected_sha256'],
             'Extension evidence changed')
    return raw


def _read(descriptor):
    descriptor = file_descriptor(descriptor)
    try:
        with FileGuard(descriptor['file']) as guard:
            raw = _guard_bytes(guard, descriptor, JSON_LIMIT)
            # Shared reader rejects duplicate JSON keys; guard protects generation.
            return read_json(descriptor['file']), raw
    except ProviderError:
        raise
    except Exception:
        raise ProviderError('Extension JSON evidence unavailable or invalid') from None


def _unique(descriptors, ledger_file):
    selected = {}
    for value in descriptors:
        descriptor = file_descriptor(value)
        key = canonical_path(descriptor['file'])
        _require(key != ledger_file, 'Immutable evidence aliases mutable ledger')
        prior = selected.get(key)
        _require(prior is None or prior['expected_sha256'] == descriptor['expected_sha256'],
                 'Evidence path has conflicting hashes')
        selected[key] = descriptor
    return [selected[key] for key in sorted(selected)]


def _inspect(source_descriptor, request_descriptor):
    source = file_descriptor(source_descriptor); request_file = file_descriptor(request_descriptor)
    ledger_file = canonical_path(source['file'])
    try:
        # No PathLocks/file creation in prepare. Actual open denies write/delete.
        with FileGuard(source['file']) as guard:
            raw = _guard_bytes(guard, source, JSON_LIMIT)
            data = BudgetLedger(source['file'])._read()
        _require(data['schema_version'] == '1.1' and data['halted'] is False,
                 'Extension requires an existing non-halted schema-1.1 ledger')
        approved = data['approval']
        _require(approved['total_cap_credits'] == 400 and approved['max_attempts'] == 10,
                 'Extension supports only unchanged cumulative 400-credit/10-attempt limits')
        rows = [*data['legacy_history'], *data['attempts']]
        _require(all(row['state'] in TERMINAL and 'accounted_credits' in row for row in rows),
                 'Every attempt must be terminal and accounted before extending approval')
        total = BudgetLedger._total(data); used = BudgetLedger._attempts_used(data)
        _require(total < 400 and used < 10, 'No cumulative budget/attempt allowance remains')
        request_value, _ = _read(request_file)
        request = normalize_provider_request(request_value)
        _require(request['operation'] in GENERATION, 'Only one new independent generation is supported')
        prior_ids = set(SLOTS) | {row['operation_id'] for row in approved['operations']} | {row['operation_id'] for row in rows}
        _require(request['operation_id'] not in prior_ids, 'New operation identity already exists or is legacy')
        root = data['legacy_root']
        legacy, _ = inspect_legacy(root['backup_descriptor'], root['review_descriptor'], ledger_file)
        evidence = _unique([request_file, root['backup_descriptor'], *legacy['evidence_descriptors'],
                            *[{'file':item['file'], 'expected_sha256':item['expected_sha256']}
                              for item in request['inputs']]], ledger_file)
        for descriptor in evidence:
            with FileGuard(descriptor['file']) as held:
                _guard_bytes(held, descriptor, INPUT_LIMIT)
        inspection = {'ledger_file':ledger_file, 'source_sha256':source['expected_sha256'],
                      'source_bytes':len(raw), 'ledger':deepcopy(data), 'request':request,
                      'request_sha256':request_sha256(request), 'evidence_descriptors':evidence,
                      'credits_before':total, 'attempts_before':used,
                      'remaining_credits':400-total, 'remaining_attempts':10-used}
        return inspection, raw
    except ProviderError:
        raise
    except Exception:
        raise ProviderError('Extension source or evidence is unsupported or incomplete') from None


@dataclass(frozen=True)
class PreparedExtension:
    source_descriptor: dict
    request_descriptor: dict
    inspection: dict

    def document(self):
        plan = self.inspection
        return {'operation':'prepare_authorization_extension', 'status':'prepared_only',
                'source_descriptor':deepcopy(self.source_descriptor),
                'request_descriptor':deepcopy(self.request_descriptor),
                'request':deepcopy(plan['request']), 'request_sha256':plan['request_sha256'],
                'ledger_file':plan['ledger_file'], 'credits_before':plan['credits_before'],
                'attempts_before':plan['attempts_before'], 'remaining_credits':plan['remaining_credits'],
                'remaining_attempts':plan['remaining_attempts'], 'fixed_cap_credits':400,
                'fixed_attempt_cap':10, 'replace_fields':list(CHANGED_FIELDS),
                'backup_plan':{'complete_original_bytes':True, 'bytes':plan['source_bytes'],
                               'expected_sha256':plan['source_sha256'], 'exclusive_create':True,
                               'location':'Caller-specified absolute audit directory; unique .source-bytes'},
                'new_trusted_authorization_and_descriptor_required':True,
                'network_calls':0, 'source_writes':0}


def prepare(source_descriptor, request_descriptor):
    """Read-only plan; no approved=true document, receipt, ledger or lock-file writes."""
    source = file_descriptor(source_descriptor); request = file_descriptor(request_descriptor)
    inspection, _ = _inspect(source, request)
    return PreparedExtension(source, request, inspection)


def _approval(prepared, authorization, descriptor):
    _require(isinstance(authorization, TrustedAuthorization), 'Separately trusted authorization is required')
    descriptor = file_descriptor(descriptor)
    observed, _ = _read(descriptor)
    TrustedAuthorization.validate(observed)
    observed['ledger_file'] = canonical_path(observed['ledger_file'])
    _require(observed == authorization.document and descriptor['expected_sha256'] == sha(authorization.approval_sha256),
             'Trusted authorization differs from its immutable approval descriptor')
    old = prepared.inspection['ledger']['approval']; new = observed
    for field in ('schema_version','approved','provider','base_url','ledger_file','total_cap_credits','max_attempts','allows_upload'):
        _require(new[field] == old[field], 'Extension changes the existing approval boundary')
    _require(new['approval_id'] != old['approval_id'], 'New approval identity must differ from previous approval')
    prefix = old['operations']; policies = new['operations']
    _require(len(policies) == len(prefix)+1 and policies[:-1] == prefix,
             'Extension must preserve every old operation as an exact ordered prefix')
    operation = policies[-1]; request = prepared.inspection['request']
    _require(operation['operation'] in GENERATION and operation['operation_id'] == request['operation_id']
             and operation['operation'] == request['operation']
             and operation['request_sha256'] == prepared.inspection['request_sha256'],
             'Added operation must match the exact prepared independent-generation request')
    _require(operation['operation_id'] != new['approval_id'], 'Operation and approval identities must be distinct')
    _require(operation['max_credits'] <= prepared.inspection['remaining_credits'],
             'New per-operation allowance exceeds the remaining cumulative budget')
    return deepcopy(new)


def _write_new(path, raw):
    with Path(path).open('xb') as stream:
        stream.write(raw); stream.flush(); os.fsync(stream.fileno())


def _encoded(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n').encode('utf-8')


def apply(prepared, authorization, authorization_descriptor, audit_directory):
    """One same-path CAS, exclusive full backup and prepared/applied receipts.

    PathLocks protect cooperating writers. Evidence remains FileGuard-held through
    replacement. The ledger guard must close before replace; this is not an OS
    atomic CAS against arbitrary non-cooperating writers. Never restore on error.
    """
    _require(isinstance(prepared, PreparedExtension), 'Prepared extension evidence is required')
    source = file_descriptor(prepared.source_descriptor); request = file_descriptor(prepared.request_descriptor)
    ledger = Path(source['file']); audit = Path(audit_directory)
    _require(audit.is_absolute() and canonical_path(str(audit)) != canonical_path(str(ledger)),
             'Audit directory must be absolute and distinct from ledger')
    fresh, _ = _inspect(source, request)
    _require(fresh == prepared.inspection, 'Prepared extension changed; prepare the current ledger again')
    approved = _approval(prepared, authorization, authorization_descriptor)
    approval_file = file_descriptor(authorization_descriptor)
    evidence = _unique([*fresh['evidence_descriptors'], approval_file], fresh['ledger_file'])
    with PathLocks([ledger, *[row['file'] for row in evidence]]):
        with ExitStack() as held:
            for descriptor in evidence:
                guard = held.enter_context(FileGuard(descriptor['file']))
                _guard_bytes(guard, descriptor, INPUT_LIMIT)
            current, original = _inspect(source, request)
            _require(current == prepared.inspection, 'Ledger/evidence changed at extension CAS')
            _require(_approval(prepared, authorization, approval_file) == approved,
                     'Approval changed at extension CAS')
            updated = deepcopy(current['ledger'])
            updated['approval'] = approved; updated['approval_sha256'] = approval_file['expected_sha256']
            _require({k:v for k,v in updated.items() if k not in CHANGED_FIELDS}
                     == {k:v for k,v in current['ledger'].items() if k not in CHANGED_FIELDS},
                     'Extension changed non-approval financial fields')
            audit.mkdir(parents=True, exist_ok=True)
            token = uuid.uuid4().hex
            backup_path = audit / ('authorization-extension-'+token+'.source-bytes')
            _write_new(backup_path, original)
            backup = {'file':str(backup_path.resolve()), 'expected_sha256':source['expected_sha256']}
            guard = held.enter_context(FileGuard(backup_path))
            _guard_bytes(guard, backup, JSON_LIMIT)
            after_sha = hashlib.sha256(_encoded(updated)).hexdigest()
            receipt = {'operation':'apply_authorization_extension', 'status':'prepared_for_cas',
                       'created_at':time.time(), 'ledger_file':current['ledger_file'],
                       'before_sha256':source['expected_sha256'], 'expected_after_sha256':after_sha,
                       'backup_descriptor':backup, 'approval_descriptor':approval_file,
                       'request_descriptor':request, 'request_sha256':current['request_sha256'],
                       'new_operation':deepcopy(approved['operations'][-1]), 'replace_fields':list(CHANGED_FIELDS),
                       'credits_before':current['credits_before'], 'attempts_before':current['attempts_before'],
                       'network_calls':0, 'new_financial_attempts':0,
                       'recovery':'Inspect current SHA/state and prepared receipt after ambiguity; never automatically restore backup after new attempts/accounting'}
            receipt_path = audit / ('authorization-extension-'+token+'.prepared.json')
            _write_new(receipt_path, _encoded(receipt))
            _require(digest(ledger) == source['expected_sha256'], 'Ledger changed before atomic approval replacement')
            atomic_json(ledger, updated)
            _require(digest(ledger) == after_sha, 'Ledger changed after replacement; preserve current financial state')
            snapshot = BudgetLedger(ledger, authorization).snapshot()
            _require(snapshot['ledger'] == updated
                     and snapshot['worst_or_accounted_credits'] == current['credits_before']
                     and snapshot['attempts_used'] == current['attempts_before'],
                     'Post-extension verification failed; preserve current financial state')
            receipt.update(status='applied', after_sha256=after_sha,
                           credits_after=snapshot['worst_or_accounted_credits'], attempts_after=snapshot['attempts_used'])
            complete = audit / ('authorization-extension-'+token+'.applied.json')
            _write_new(complete, _encoded(receipt))
            return {**receipt, 'receipt_file':str(complete.resolve()), 'prepared_receipt_file':str(receipt_path.resolve())}
