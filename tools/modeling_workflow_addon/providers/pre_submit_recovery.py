"""One-shot recovery of a caller-proven, never-posted generation reservation.

The caller explicitly verifies the stopped original job/owner, original source,
and absence of the original generation POST. A reserved row, time, or a SHA does
not establish these facts or authenticate a human. Original submission approval
does not grant recovery approval. Only cooperative writers honor PathLocks;
prepared receipts/backups support inspection, never automatic financial rollback.
"""
from contextlib import ExitStack, contextmanager
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import time
import uuid

from ..contract import (canonical_path, file_descriptor, identity,
                        normalize_pre_submit_recovery_document, normalize_provider_request,
                        request_sha256, sha)
from .budget import BudgetLedger, TERMINAL, TrustedAuthorization, _guarded_descriptor
from .common import FileGuard, PathLocks, ProviderError, RateLimitError, atomic_json, read_json
from .transport_diagnostics import SafeProviderError
from protocol import Failure

JSON_LIMIT = 2 * 1024 * 1024
EVIDENCE_LIMIT = 32 * 1024 * 1024


def _require(condition, message):
    if not condition:
        raise ProviderError(message)


@dataclass(frozen=True)
class TrustedPreSubmitRecovery:
    """Caller declaration of fresh human recovery approval and reviewed proof.

    The descriptor hash binds the declaration bytes, not the approving person.
    Prior owner/source/post evidence is reviewed by the caller, not inferred here.
    """
    document: dict
    recovery_sha256: str
    descriptor: dict

    @classmethod
    def from_descriptor(cls, descriptor):
        descriptor = file_descriptor(descriptor)
        document = normalize_pre_submit_recovery_document(_guarded_descriptor(descriptor, JSON_LIMIT, True))
        _guarded_descriptor(document['verification']['evidence_descriptor'], EVIDENCE_LIMIT)
        return cls(document, descriptor['expected_sha256'], descriptor)

    def verify(self):
        current = self.from_descriptor(self.descriptor)
        _require(current.document == self.document and current.recovery_sha256 == self.recovery_sha256,
                 'Reviewed pre-submit recovery statement changed')
        return current.document


def _encoded(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf-8')


def _write_new(path, raw):
    with Path(path).open('xb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def _bytes(guard, expected_sha256, limit):
    blocks = []; size = 0
    for block in guard.chunks():
        size += len(block)
        _require(size <= limit, 'Pre-submit recovery local evidence exceeds size bound')
        blocks.append(block)
    raw = b''.join(blocks)
    _require(bool(raw) and hashlib.sha256(raw).hexdigest() == expected_sha256,
             'Pre-submit recovery local evidence changed')
    return raw


def _identity(guard):
    value = guard.identity()
    return value['volume'], value['file_index']


def _authorization(authorization, descriptor):
    _require(isinstance(authorization, TrustedAuthorization), 'Caller-trusted original authorization required')
    descriptor = file_descriptor(descriptor)
    current = TrustedAuthorization.from_descriptor(descriptor)
    _require(current.document == authorization.document
             and current.approval_sha256 == sha(authorization.approval_sha256),
             'Original authorization differs from immutable approval evidence')
    return current


@contextmanager
def held_inputs(ledger, request, authorization_descriptor, recovery):
    """Keep immutable statement/evidence/images/auth open, never mutable ledger.

    Reject both path and hard-link aliases before any ledger mutation. The public
    host independently holds all running implementation sources for its lifecycle.
    """
    _require(isinstance(recovery, TrustedPreSubmitRecovery), 'Caller-reviewed recovery approval required')
    statement = recovery.verify()
    descriptors = [recovery.descriptor, statement['verification']['evidence_descriptor'],
                   file_descriptor(authorization_descriptor),
                   *[file_descriptor({k: row[k] for k in ('file', 'expected_sha256')})
                     for row in request.get('inputs', [])]]
    _require(ledger.path.is_file(), 'Pre-submit recovery requires an existing ledger; never create it')
    with FileGuard(ledger.path) as guard:
        _bytes(guard, statement['expected_ledger_sha256'], JSON_LIMIT)
        data = ledger._read()
        ledger_identity = _identity(guard)
    if data['schema_version'] == '1.2':
        root = data['outstanding_increment']
        original_approval = file_descriptor(authorization_descriptor)
        derived_approval = file_descriptor(root['approval_descriptor'])
        _require(canonical_path(original_approval['file']) == canonical_path(derived_approval['file'])
                 and original_approval['expected_sha256'] == derived_approval['expected_sha256'],
                 'Increment recovery requires its original derived approval descriptor')
        descriptors += [root[key] for key in ('backup_descriptor', 'authorization_descriptor',
                                              'request_descriptor', 'approval_descriptor')]
    selected = {}
    ledger_name = canonical_path(str(ledger.path))
    for value in descriptors:
        descriptor = file_descriptor(value)
        name = canonical_path(descriptor['file'])
        _require(name != ledger_name, 'Immutable recovery evidence aliases mutable ledger')
        _require(name not in selected or selected[name]['expected_sha256'] == descriptor['expected_sha256'],
                 'Recovery evidence path has conflicting hashes')
        selected[name] = descriptor
    try:
        with ExitStack() as held:
            identities = {}
            for name, descriptor in sorted(selected.items()):
                guard = held.enter_context(FileGuard(descriptor['file']))
                current = _identity(guard)
                _require(current != ledger_identity, 'Immutable recovery evidence hard-links mutable ledger')
                _require(current not in identities or identities[current] == name,
                         'Recovery evidence contains hard-link aliases')
                identities[current] = name
                _bytes(guard, descriptor['expected_sha256'], EVIDENCE_LIMIT)
            yield
    except (ProviderError, KeyboardInterrupt, SystemExit, Failure):
        raise
    except Exception as exc:
        error = ProviderError('Pre-submit recovery local evidence unavailable or invalid')
        if isinstance(getattr(exc, 'pre_submit_recovery', None), dict):
            error.pre_submit_recovery = deepcopy(exc.pre_submit_recovery)
        raise error from None


def _inspect(ledger, request, authorization, recovery):
    """Existing-only read. Called again with the ledger PathLock held for CAS."""
    from .tripo_cn import GENERATION, RESERVATION_MINIMUM_CREDITS
    _require(isinstance(ledger, BudgetLedger), 'Existing budget ledger required')
    _require(isinstance(recovery, TrustedPreSubmitRecovery), 'Caller-reviewed recovery approval required')
    _require(isinstance(authorization, TrustedAuthorization), 'Original trusted authorization required')
    request = normalize_provider_request(request)
    statement = recovery.verify()
    _require(request['operation'] in GENERATION, 'Recovery only permits original generation')
    _require(canonical_path(str(ledger.path)) == statement['ledger_file']
             == canonical_path(authorization.document['ledger_file']), 'Recovery ledger path mismatch')
    _require(ledger.path.is_file(), 'Pre-submit recovery requires an existing ledger; never create it')
    # A temporary guard binds _read and complete old bytes to this generation,
    # then closes before the operation atomically replaces the mutable ledger.
    try:
        with FileGuard(ledger.path) as guard:
            raw = _bytes(guard, statement['expected_ledger_sha256'], JSON_LIMIT)
            data = ledger._read()
    except ProviderError:
        raise
    except Exception:
        raise ProviderError('Pre-submit recovery existing ledger unavailable or invalid') from None
    expected_schema = statement.get('ledger_schema_version', '1.0')
    _require(data['schema_version'] == expected_schema and data['halted'] is False,
             'Pre-submit recovery requires its explicitly scoped non-halted ledger version')
    _require(data['approval'] == authorization.document
             and data['approval_sha256'] == authorization.approval_sha256 == statement['approval_sha256'],
             'Recovery original approval does not match existing ledger')
    request_hash = request_sha256(request)
    policy = authorization.operation(request, request_hash)
    _require(authorization.document['allows_upload'] is True,
             'Original authorization does not permit image upload')
    scope = data['attempts']
    if expected_schema == '1.2':
        # _read already validated the entire increment root and old ordered
        # prefix. Re-read its exact guarded backup to identify the extra row;
        # never infer the prefix length from the number of frozen unknowns.
        root = data['outstanding_increment']
        original = _guarded_descriptor(root['backup_descriptor'], JSON_LIMIT, True)
        prefix = original['attempts']
        _require(data['attempts'][:len(prefix)] == prefix,
                 'Increment recovery cannot change any original attempt')
        scope = data['attempts'][len(prefix):]
        _require(len(scope) == 1 and root['new_operation'] == policy,
                 'Increment recovery requires its single exact added reservation')
    unresolved = [row for row in scope if row['state'] not in TERMINAL]
    _require(len(unresolved) == 1, 'Recovery requires exactly one unresolved original reservation')
    row = unresolved[0]
    _require(row['id'] == statement['attempt_id'] and row['state'] == 'reserved'
             and row['task_id'] is None
             and not any(key in row for key in ('accounted_credits', 'receipt_sha256', 'binding_receipt', 'pre_submit_recovery_receipt')),
             'Recovery attempt is not an untouched reserved pre-submit attempt')
    _require(all(other is row or (other['state'] in TERMINAL and 'accounted_credits' in other)
                 for other in scope), 'Every other recoverable attempt must be terminal and accounted')
    _require(all(source['operation_id'] == request['operation_id']
                 and source['operation'] == request['operation']
                 and source['request_sha256'] == request_hash for source in (row, policy, statement)),
             'Recovery differs from original approved operation/request')
    _require(row['reserved_credits'] == policy['max_credits']
             and row['reserved_credits'] >= RESERVATION_MINIMUM_CREDITS[request['operation']],
             'Original reservation does not cover the current operation price floor')
    return data, row, policy, raw


def _validate_candidate(ledger, before, updated, attempt_id):
    """Validate prospective 1.2 bytes before any direct recovery write."""
    if before['schema_version'] != '1.2':
        return
    _require({k: v for k, v in before.items() if k != 'attempts'}
             == {k: v for k, v in updated.items() if k != 'attempts'},
             'Recovery cannot change increment root, approval or halt')
    _require(len(before['attempts']) == len(updated['attempts'])
             and [row['id'] for row in before['attempts']] == [row['id'] for row in updated['attempts']]
             and all(old == new for old, new in zip(before['attempts'], updated['attempts'])
                     if old['id'] != attempt_id)
             and BudgetLedger._total(before) == BudgetLedger._total(updated),
             'Recovery cannot change other attempts, order, cost or attempt count')
    old = next(row for row in before['attempts'] if row['id'] == attempt_id)
    new = next(row for row in updated['attempts'] if row['id'] == attempt_id)
    mutable = {'state', 'observed_at', 'pre_submit_recovery_receipt'}
    _require({k: v for k, v in old.items() if k not in mutable}
             == {k: v for k, v in new.items() if k not in mutable},
             'Recovery cannot change the target reservation identity or financial fields')
    if old['state'] == 'reserved':
        _require(new['state'] == 'submitting' and 'pre_submit_recovery_receipt' in new,
                 'Recovery claim must preserve its once-only receipt')
    else:
        _require(old['state'] == 'submitting' and new['state'] == 'unknown'
                 and old.get('pre_submit_recovery_receipt') == new.get('pre_submit_recovery_receipt'),
                 'Recovery freeze must preserve the exact claim receipt')
    ledger._validate(updated)


def preflight(ledger, request, authorization, recovery, authorization_descriptor):
    """Local validation only; never initializes/reserves or contacts a provider."""
    from .tripo_cn import _png
    request = normalize_provider_request(request)
    with held_inputs(ledger, request, authorization_descriptor, recovery):
        authorization = _authorization(authorization, authorization_descriptor)
        with PathLocks([ledger.path]):
            data, row, policy, _ = _inspect(ledger, request, authorization, recovery)
        files = [_png(item) for item in request['inputs']]
    return request, deepcopy(row), deepcopy(policy), files


def claim(ledger, request, authorization, recovery, authorization_descriptor, audit_directory):
    """One cooperative-path CAS; preserves caps, approval, old rows and cost/count.

    Never calls BudgetLedger(path, authorization): a missing ledger must stay
    absent. Replacement ambiguity retains full backup/prepared receipt and never
    automatically rolls back. Non-cooperative writers/power failure are outside
    this local CAS guarantee.
    """
    request = normalize_provider_request(request)
    audit = Path(audit_directory)
    _require(audit.is_absolute(), 'Recovery audit directory must be absolute')
    with held_inputs(ledger, request, authorization_descriptor, recovery):
        authorization = _authorization(authorization, authorization_descriptor)
        # Only after trusted original authorization validation, without constructor writes.
        ledger.authorization = authorization
        with PathLocks([ledger.path]):
            data, row, policy, original = _inspect(ledger, request, authorization, recovery)
            receipt = {'recovery_sha256': recovery.recovery_sha256,
                       'evidence_sha256': recovery.document['verification']['evidence_descriptor']['expected_sha256'],
                       'prior_implementation_sha256': recovery.document['prior_implementation_sha256'],
                       'request_sha256': row['request_sha256'], 'claim_nonce': uuid.uuid4().hex,
                       'claimed_at': time.time()}
            updated = deepcopy(data)
            claimed = next(item for item in updated['attempts'] if item['id'] == row['id'])
            claimed.update(state='submitting', observed_at=receipt['claimed_at'], pre_submit_recovery_receipt=receipt)
            _validate_candidate(ledger, data, updated, row['id'])
            audit = audit.resolve(); audit.mkdir(parents=True, exist_ok=True)
            token = receipt['claim_nonce']; before = hashlib.sha256(original).hexdigest()
            after = hashlib.sha256(_encoded(updated)).hexdigest()
            backup_path = audit / ('pre-submit-' + token + '.source-bytes')
            _write_new(backup_path, original)
            backup = {'file': str(backup_path), 'expected_sha256': before}
            prepared_path = audit / ('pre-submit-' + token + '.prepared.json')
            location = {'ledger_file': str(ledger.path), 'attempt_id': row['id'],
                        'claim_nonce': token, 'before_sha256': before, 'expected_after_sha256': after,
                        'backup_descriptor': backup, 'prepared_receipt_file': str(prepared_path),
                        'claim_receipt': deepcopy(receipt), 'new_financial_attempts': 0,
                        'mutation_outcome': 'inspect_current_ledger', 'automatic_retry': False,
                        'recovery': 'Inspect ledger and prepared receipt; never restore financial backup or repeat recovery automatically'}
            _write_new(prepared_path, _encoded({'operation': 'resume_pre_submit', 'status': 'prepared_for_claim', **location}))
            try:
                # Re-read both bytes and structure under the same cooperative lock.
                current, _, _, current_raw = _inspect(ledger, request, authorization, recovery)
                _require(current == data and current_raw == original, 'Ledger changed before pre-submit claim')
                atomic_json(ledger.path, updated)
                with FileGuard(ledger.path) as guard:
                    _bytes(guard, after, JSON_LIMIT)
                    _require(ledger._read() == updated, 'Pre-submit claim postcheck failed')
                return {'row': deepcopy(claimed), 'pre_submit_recovery': location}
            except BaseException as exc:
                exc.pre_submit_recovery = deepcopy(location)
                raise


def _freeze_unknown(ledger, location):
    """Nonce-matched submitting only; preserve confirmation and all later states."""
    with PathLocks([ledger.path]):
        data = ledger._read()
        found = [row for row in data['attempts'] if row['id'] == location['attempt_id']]
        _require(len(found) == 1, 'Recovery claim attempt unavailable for conservative freeze')
        row = found[0]
        if (row['state'] == 'submitting'
                and row.get('pre_submit_recovery_receipt', {}).get('claim_nonce') == location['claim_nonce']):
            before = deepcopy(data)
            row.update(state='unknown', observed_at=time.time())
            _validate_candidate(ledger, before, data, row['id'])
            atomic_json(ledger.path, data)
        return row['state']


def _retain_acceptance(location, request, task_id, model):
    """Retain only a validated acceptance identity, never the remote response.

    Populate the in-memory locator before any I/O. The exclusive receipt is a
    separate observation, not ledger confirmation, mapping authority or cost
    settlement. Its expected descriptor exists even when persistence fails;
    persisted=True requires successful write/fsync plus matching bytes and JSON.
    """
    location['accepted_task_id'] = task_id
    # Reuse the already-resolved audit directory from claim, without new path
    # resolution I/O before the accepted identity/path are retained. Keep the
    # basename shorter than the prepared receipt for legal Windows paths.
    receipt_path = Path(location['prepared_receipt_file']).with_name(location['claim_nonce'] + '.acceptance.json')
    location['acceptance_receipt_file'] = str(receipt_path)
    location['acceptance_receipt_persisted'] = False
    receipt = {'provider': 'tripo-cn', 'operation': request['operation'],
               'attempt_id': location['attempt_id'], 'request_sha256': request_sha256(request),
               'task_id': task_id, 'model': model, 'claim_nonce': location['claim_nonce']}
    raw = _encoded(receipt)
    descriptor = {'file': str(receipt_path), 'expected_sha256': hashlib.sha256(raw).hexdigest()}
    location['acceptance_receipt_descriptor'] = descriptor
    _write_new(receipt_path, raw)
    with FileGuard(receipt_path) as guard:
        _bytes(guard, descriptor['expected_sha256'], JSON_LIMIT)
        _require(read_json(receipt_path) == receipt, 'Acceptance observation receipt verification failed')
    location['acceptance_receipt_persisted'] = True


def resume(provider, request, authorization, recovery, authorization_descriptor, audit_directory,
           *, checkpoint=lambda: None):
    """At most one original generation POST after claiming the same attempt."""
    from .tripo_cn import MODELS, ROUTES
    from .image_upload import UploadError
    checkpoint()
    provider.upload_receipts = []
    request = normalize_provider_request(request)
    ledger = provider.ledger
    with held_inputs(ledger, request, authorization_descriptor, recovery):
        request, row, policy, files = preflight(ledger, request, authorization, recovery, authorization_descriptor)
        checkpoint()
        available = provider._api('/account/balance').get('balance')
        _require(type(available) in (int, float) and math.isfinite(available) and available >= policy['max_credits'],
                 'Available provider credits do not cover original reservation; no recharge')
        checkpoint()
        location = None
        try:
            claimed = claim(ledger, request, authorization, recovery, authorization_descriptor, audit_directory)
            location = claimed['pre_submit_recovery']
            checkpoint()
            tokens = []
            for raw in files:
                checkpoint()
                tokens.append(provider._upload_reference(raw, checkpoint=checkpoint))
            op = request['operation']
            body = {'model': MODELS[op], 'texture': False, 'pbr': False}
            if op == 'h31_parts_multiview':
                body['generate_parts'] = True
            if op == 'h31_image':
                body['input'] = tokens[0]
            else:
                body['inputs'] = [{'front': tokens[0]}, {'back': tokens[1]}]
            checkpoint()
            reply = provider._api(ROUTES[op], body)
            task_id = identity(reply.get('task_id'))
            location['accepted_task_id'] = task_id
            _retain_acceptance(location, request, task_id, MODELS[op])
            # Persist an accepted task identity before observing cancellation.
            # A cancellation signal is not evidence that a known reply vanished.
            row = ledger.update(row['id'], 'confirmed', task_id=task_id)
            checkpoint()
            return {'provider': 'tripo-cn', 'operation': op, 'operation_id': request['operation_id'],
                    'attempt_id': row['id'], 'task_id': task_id, 'state': row['state'],
                    'request_sha256': row['request_sha256'], 'reserved_credits': row['reserved_credits'],
                    'pre_submit_recovery': location, 'uploads': list(provider.upload_receipts)}
        except BaseException as exc:
            location = location or getattr(exc, 'pre_submit_recovery', None)
            if location:
                try:
                    location['observed_state'] = _freeze_unknown(ledger, location)
                except BaseException:
                    location['observed_state'] = 'inspect_current_ledger'
                    location['unknown_write_failed'] = True
                if isinstance(exc, (KeyboardInterrupt, SystemExit, Failure, SafeProviderError, RateLimitError, UploadError)):
                    exc.pre_submit_recovery = deepcopy(location)
                    raise
                error = ProviderError('Pre-submit recovery consumed or outcome uncertain; reservation retained; never repeat recovery')
                error.pre_submit_recovery = deepcopy(location)
                raise error from None
            raise
