"""Bounded Q28 six-task migration. Offline evidence is integrity, not human approval.

prepare only reads. apply requires a separately obtained TrustedAuthorization,
same-path CAS and preserved original bytes. No network, parent-task import,
approval creation, auto rollback, retries, or legacy request-hash rewriting.
"""
from contextlib import ExitStack
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import time
import uuid

from ..contract import canonical_path, file_descriptor, identity, integer, obj, sha
from .common import ProviderError, FileGuard, PathLocks, atomic_json, digest

SLOTS = ('G1', 'G2', 'G3', 'S1', 'T1', 'C1')
LIMITS = dict(zip(SLOTS, (20, 20, 100, 40, 20, 50)))
GENERATION = {'h31_image', 'h31_multiview', 'p2_multiview'}
EXPECTED = {
    'G1': ('/generation/image-to-model', 'v3.1-20260211', 'image_to_model'),
    'G2': ('/generation/multiview-to-model', 'v3.1-20260211', 'multiview_to_model'),
    'G3': ('/generation/multiview-to-model', 'P2-20260801', 'multiview_to_model'),
    'S1': ('/mesh/segment', 'v2.0-20260430', 'mesh_segmentation'),
    'T1': ('/models/texture', 'v3.5-20260815', 'texture_model'),
    'C1': ('/mesh/complete', 'v1.0-20250506', 'mesh_completion'),
}
BASE = 'https://openapi.tripo3d.com/v3'


def _require(condition, message):
    if not condition:
        raise ProviderError(message)


def _canonical_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def _descriptor(value):
    if set(value) == {'file', 'sha256'}:
        value = {'file': value['file'], 'expected_sha256': value['sha256']}
    return file_descriptor(value)


def _read(value):
    descriptor = _descriptor(value)
    try:
        with FileGuard(descriptor['file']) as guard:
            blocks = []; size = 0
            for block in guard.chunks():
                size += len(block)
                _require(size <= 2 * 1024 * 1024, 'Legacy evidence exceeds size bound')
                blocks.append(block)
            raw = b''.join(blocks)
            _require(raw and hashlib.sha256(raw).hexdigest() == descriptor['expected_sha256'],
                     'Legacy evidence changed')
            return json.loads(raw.decode('utf-8-sig')), raw
    except ProviderError:
        raise
    except Exception:
        raise ProviderError('Legacy evidence unavailable or invalid') from None


def _inspect(source_descriptor, review_descriptor, original_path):
    source_descriptor = _descriptor(source_descriptor); review_descriptor = _descriptor(review_descriptor)
    original_path = canonical_path(str(original_path))
    old, raw = _read(source_descriptor); review, _ = _read(review_descriptor)
    _require(review.get('status') == 'passed' and review.get('mode') == 'pure_read_only_offline'
             and review.get('original_ledger_sha256') == source_descriptor['expected_sha256']
             and review.get('credits_used') == 250 and review.get('attempts_used') == 6,
             'Independent legacy review does not match this source')
    inputs = [_descriptor(value) for value in review['inputs']]
    _require(len({canonical_path(value['file']) for value in inputs}) == len(inputs), 'Duplicate legacy review input')
    sources = [value for value in inputs if canonical_path(value['file']) == original_path]
    _require(len(sources) == 1 and sources[0]['expected_sha256'] == source_descriptor['expected_sha256'],
             'Legacy review is bound to another ledger path')
    plans = [value for value in inputs if value['expected_sha256'] == old.get('plan_sha256')]
    _require(len(plans) == 1, 'Original Q28 root evidence is missing or ambiguous')
    plan_descriptor = plans[0]; plan, _ = _read(plan_descriptor)
    obj(old, ('ledger_version', 'approval_question', 'plan_sha256', 'region', 'max_credits',
              'max_attempts', 'attempts', 'halted'),
             ('ledger_version', 'approval_question', 'plan_sha256', 'region', 'max_credits',
              'max_attempts', 'attempts', 'halted'))
    _require(type(old['ledger_version']) is int and old['ledger_version'] == 1
             and old['approval_question'] == 'Q28' and old['region'] == 'cn'
             and type(old['max_credits']) is int and old['max_credits'] == 400
             and type(old['max_attempts']) is int and old['max_attempts'] == 10
             and old['halted'] is False, 'Legacy root/cap/halt identity is unsupported')
    approval = plan.get('user_approval', {})
    _require(plan.get('status') == 'approved_by_user_Q28' and plan.get('region') == 'cn'
             and plan.get('base_url') == BASE and plan.get('hard_cap_credits') == 400
             and plan.get('max_billable_submission_attempts') == 10 and approval.get('question') == 'Q28'
             and approval.get('max_credits') == 400 and approval.get('max_submission_attempts') == 10
             and approval.get('allows_upload') is True and approval.get('allows_recharge') is False,
             'Original Q28 authorization is unsupported')
    policies = {value['id']: value for value in plan['base_steps']}
    _require(len(plan['base_steps']) == 7 and set(policies) == set(SLOTS) | {'X1'}
             and {key: policies[key]['credits_max'] for key in (*SLOTS, 'X1')} == {**LIMITS, 'X1': 10}
             and all(value['submissions_max'] == 1 for value in policies.values()), 'Q28 base limits differ')
    _require({value['kind']: value['credits_max'] for value in plan['bounded_redo']}
             == {'generation': 100, 'texture': 20, 'convert': 10}
             and len(plan['bounded_redo']) == 3
             and all(value['submissions_max'] == 1 for value in plan['bounded_redo']), 'Q28 redo limits differ')
    rows = old['attempts']; maps = review['mapping']
    _require(isinstance(rows, list) and len(rows) == 6 and [value['operation'] for value in rows] == list(SLOTS)
             and isinstance(maps, list) and len(maps) == 6, 'Only the reviewed settled six-task trial is supported')
    mapped = {value['legacy_operation']: value for value in maps}
    _require(set(mapped) == set(SLOTS), 'Legacy review mappings differ')
    tasks = {row['operation']: identity(row['task_id']) for row in rows}
    _require(len(set(tasks.values())) == 6 and len({identity(row['id']) for row in rows}) == 6,
             'Duplicate historical task or attempt')
    history = []; evidence = [review_descriptor, plan_descriptor]
    for row in rows:
        slot = row['operation']; item = mapped[slot]
        allowed = ('id', 'operation', 'request_sha256', 'reserved_credits', 'remaining_required_credits',
                   'state', 'task_id', 'created_at', 'accounting', 'submitted_at', 'observed_at', 'accounted_credits')
        obj(row, allowed, allowed)
        integer(row['remaining_required_credits'], 0, 400)
        for field in ('created_at', 'submitted_at', 'observed_at'):
            _require(type(row[field]) in (int, float) and math.isfinite(row[field]) and row[field] >= 0,
                     'Invalid historical timestamp')
        wire_sha = sha(row['request_sha256'])
        _require(row['state'] == 'succeeded' and row['reserved_credits'] == LIMITS[slot]
                 and row['accounted_credits'] == LIMITS[slot]
                 and item['original_attempt_id'] == row['id'] and item['task_id'] == row['task_id']
                 and item['legacy_request_sha256'] == wire_sha and item['credits'] == LIMITS[slot],
                 'Historical operation, terminal state or finance differs')
        integer(row['reserved_credits'], 1, 400); integer(row['accounted_credits'], 0, 400)
        request_descriptor = _descriptor(item['request_descriptor']); receipt_descriptor = _descriptor(item['receipt_descriptor'])
        _require(request_descriptor in inputs and receipt_descriptor in inputs, 'Review lacks original request/receipt input')
        request, _ = _read(request_descriptor); receipt, _ = _read(receipt_descriptor)
        endpoint, model, task_type = EXPECTED[slot]
        body = request['body'] if slot == 'C1' else request
        _require(request['request_sha256'] == wire_sha and request['endpoint'] == endpoint and body['model'] == model
                 and policies[slot]['model'] == model, 'Historical request identity differs')
        credits = receipt.get('credits_consumed')
        _require(receipt.get('task_id') == row['task_id'] and receipt.get('type') == task_type
                 and receipt.get('status') == 'success' and type(credits) in (int, float)
                 and math.isfinite(credits) and math.ceil(credits) == row['accounted_credits'] and credits >= 0,
                 'Historical remote identity or charge differs')
        charges = row['accounting']
        _require(isinstance(charges, list) and len(charges) == 1, 'Unsupported historical accounting events')
        charge = charges[0]
        obj(charge, ('net_credits', 'evidence_sha256', 'kind', 'observed_at'),
                    ('net_credits', 'evidence_sha256', 'kind', 'observed_at'))
        _require(charge['kind'] == 'charge_receipt' and charge['net_credits'] == row['accounted_credits']
                 and sha(charge['evidence_sha256']) == receipt_descriptor['expected_sha256']
                 == item['receipt_sha256'], 'Historical accounting receipt differs')
        approval_descriptors = [plan_descriptor]
        if slot in ('G1', 'G2', 'G3'):
            _require(item['wire_digest_recomputable'] is False and request.get('texture') is False
                     and request.get('pbr') is False and list(request['views'])
                     == (['front'] if slot == 'G1' else ['front', 'back']), 'Redacted generation evidence differs')
            for view_sha in request['views'].values():
                sha(view_sha)
        else:
            _require(item['wire_digest_recomputable'] is True, 'Historical wire evidence is not reconstructable')
            if slot == 'S1':
                keys = ('input', 'model', 'segmentation_granularity', 'split_by_connectivity')
                wire = {key: request[key] for key in keys}
                _require(wire['input'] == tasks['G2'] and wire['segmentation_granularity'] == 'balanced'
                         and wire['split_by_connectivity'] is True, 'S1 parent or wire differs')
            elif slot == 'T1':
                keys = ('input', 'model', 'texture_quality', 'pbr', 'bake', 'delight')
                wire = {key: request[key] for key in keys}
                _require(wire['input'] == tasks['G2'] and wire['texture_quality'] == 'detailed'
                         and all(wire[key] is True for key in ('pbr', 'bake', 'delight')), 'T1 wire differs')
            else:
                obj(body, ('model', 'completion_mode', 'input'), ('model', 'completion_mode', 'input'))
                wire = body
                _require(body['input'] == tasks['S1'] and body['completion_mode'] == 'ai_completion', 'C1 wire differs')
            wire_bytes = (json.dumps(wire, sort_keys=True, separators=(',', ':')) if slot == 'C1'
                          else json.dumps(wire)).encode()
            _require(hashlib.sha256(wire_bytes).hexdigest() == wire_sha, 'Historical wire digest mismatch')
            if slot in ('T1', 'C1'):
                approvals = [value for value in inputs if value['expected_sha256'] == request['approval_sha256']]
                _require(len(approvals) == 1, 'Q30/Q31 evidence is missing or ambiguous')
                approved, _ = _read(approvals[0]); approval_descriptors.append(approvals[0])
                question = 'Q30' if slot == 'T1' else 'Q31'
                _require(approved['question'] == question and approved['operation'] == slot
                         and approved['source_task_id'] == wire['input'] and approved['model'] == model
                         and approved['max_credits'] == LIMITS[slot], 'Q30/Q31 operation identity differs')
                if slot == 'T1':
                    _require(approved['q28_total_cap_unchanged'] == 400 and approved['q28_attempt_limit_unchanged'] == 10,
                             'Q30 cumulative limits differ')
                else:
                    _require(approved['approved'] is True and approved['completion_mode'] == 'ai_completion'
                             and approved['part_names'] == 'omitted_all_parts' and approved['max_submissions'] == 1
                             and approved['retries'] == 0 and approved['recharge'] is False
                             and approved['automatic_texture'] is False and approved['q28_total_cap'] == 400
                             and approved['q28_max_attempts'] == 10
                             and canonical_path(approved['ledger_file']) == original_path, 'Q31 scope or path differs')
        evidence += [request_descriptor, receipt_descriptor, *approval_descriptors]
        history.append({'id': row['id'], 'operation_id': slot, 'legacy_operation': slot,
                        'task_id': row['task_id'], 'state': row['state'],
                        'wire_request_sha256': wire_sha, 'request_hash_domain': 'legacy_wire',
                        'wire_digest_recomputable': slot not in ('G1', 'G2', 'G3'),
                        'reserved_credits': row['reserved_credits'], 'accounted_credits': row['accounted_credits'],
                        'created_at': row['created_at'], 'submitted_at': row['submitted_at'], 'observed_at': row['observed_at'],
                        'remaining_required_credits': row['remaining_required_credits'], 'accounting': deepcopy(charges),
                        'request_descriptor': request_descriptor, 'receipt_descriptor': receipt_descriptor,
                        'approval_descriptors': approval_descriptors})
    _require(sum(row['accounted_credits'] for row in history) == 250, 'Historical total differs')
    unique = {canonical_path(value['file']): value for value in evidence}
    _require(original_path not in unique, 'Legacy evidence aliases the mutable ledger')
    return {'ledger_file': original_path, 'source_sha256': source_descriptor['expected_sha256'],
            'plan_descriptor': plan_descriptor, 'review_descriptor': review_descriptor,
            'history': history, 'evidence_descriptors': list(unique.values())}, raw


@dataclass(frozen=True)
class PreparedTrial:
    source_descriptor: dict
    review_descriptor: dict
    inspection: dict

    def document(self):
        return {'operation': 'prepare_legacy_trial', 'status': 'prepared_only',
                'source_descriptor': deepcopy(self.source_descriptor), 'review_descriptor': deepcopy(self.review_descriptor),
                'legacy_total_credits': 250, 'legacy_attempts_used': 6, 'legacy_root_cap': 400,
                'legacy_root_attempt_cap': 10, 'new_approval_required_for_apply': True,
                'history': deepcopy(self.inspection['history']), 'network_calls': 0, 'source_writes': 0}


def prepare(source_descriptor, review_descriptor):
    """Read-only evidence plan. Never obtains approval, locks a path or creates files."""
    source_descriptor = _descriptor(source_descriptor); review_descriptor = _descriptor(review_descriptor)
    try:
        inspection, _ = _inspect(source_descriptor, review_descriptor, source_descriptor['file'])
        return PreparedTrial(source_descriptor, review_descriptor, inspection)
    except ProviderError:
        raise
    except Exception:
        raise ProviderError('Legacy trial evidence is unsupported or incomplete') from None


def validate_history(data, ledger_path):
    """Reader re-derives immutable history from preserved bytes and source evidence."""
    root = data['legacy_root']
    fields = ('ledger_file', 'source_sha256', 'plan_descriptor', 'review_descriptor', 'backup_descriptor', 'history_sha256')
    obj(root, fields, fields)
    _require(canonical_path(str(ledger_path)) == root['ledger_file'], 'Migrated ledger path changed')
    sha(root['source_sha256']); sha(root['history_sha256'])
    backup = _descriptor(root['backup_descriptor'])
    _require(canonical_path(backup['file']) != root['ledger_file']
             and backup['expected_sha256'] == root['source_sha256'], 'Invalid legacy backup identity')
    try:
        inspection, _ = _inspect(backup, root['review_descriptor'], root['ledger_file'])
    except ProviderError:
        raise
    except Exception:
        raise ProviderError('Migrated history evidence is invalid') from None
    history = data['legacy_history']
    _require(history == inspection['history'] and _canonical_digest(history) == root['history_sha256']
             and root['plan_descriptor'] == inspection['plan_descriptor'], 'Immutable legacy history differs')
    cap = data['approval']['total_cap_credits']; maximum = data['approval']['max_attempts']
    _require(250 <= cap <= 400 and 6 <= maximum <= 10, 'Migrated cumulative limits differ')
    _require(all(row['operation'] in GENERATION and row['operation_id'] not in SLOTS
                 for row in data['approval']['operations']), 'Migration approval may only contain new independent generation')
    return history


def apply(prepared, authorization, audit_directory):
    """Same-path, evidence-guarded CAS. No automatic financial rollback on failure."""
    from .budget import BudgetLedger, TrustedAuthorization
    _require(isinstance(prepared, PreparedTrial) and isinstance(authorization, TrustedAuthorization),
             'Prepared evidence and separately trusted authorization are required')
    authorization.validate(authorization.document); sha(authorization.approval_sha256)
    source = _descriptor(prepared.source_descriptor); review = _descriptor(prepared.review_descriptor)
    ledger_path = Path(source['file']); approved = authorization.document
    _require(canonical_path(approved['ledger_file']) == canonical_path(str(ledger_path)), 'Migration approval changes ledger path')
    _require(250 <= approved['total_cap_credits'] <= 400 and 6 <= approved['max_attempts'] <= 10,
             'Migration cannot reset or enlarge cumulative limits')
    _require(len(approved['operations']) <= approved['max_attempts'] - 6
             and all(row['operation'] in GENERATION and row['operation_id'] not in SLOTS
                     and row['max_credits'] <= approved['total_cap_credits'] - 250 for row in approved['operations']),
             'Only affordable newly approved independent generation is permitted')
    inspection, _ = _inspect(source, review, source['file'])
    _require(inspection == prepared.inspection, 'Prepared legacy evidence changed')
    audit = Path(audit_directory)
    _require(audit.is_absolute() and canonical_path(str(audit)) != canonical_path(str(ledger_path)), 'Invalid migration audit directory')
    evidence = inspection['evidence_descriptors']
    with PathLocks([ledger_path, *[value['file'] for value in evidence]]):
        with ExitStack() as held:
            for descriptor in evidence:
                guard = held.enter_context(FileGuard(descriptor['file']))
                _require(guard.sha256() == descriptor['expected_sha256'], 'Migration evidence changed before CAS')
            fresh, original_bytes = _inspect(source, review, source['file'])
            _require(fresh == inspection, 'Migration evidence changed at CAS')
            audit.mkdir(parents=True, exist_ok=True)
            token = uuid.uuid4().hex
            backup_path = audit / ('legacy-' + token + '.source-bytes')
            with backup_path.open('xb') as stream:
                stream.write(original_bytes); stream.flush(); os.fsync(stream.fileno())
            backup = {'file': str(backup_path.resolve()), 'expected_sha256': source['expected_sha256']}
            root = {key: fresh[key] for key in ('ledger_file', 'source_sha256', 'plan_descriptor', 'review_descriptor')}
            root.update(backup_descriptor=backup, history_sha256=_canonical_digest(fresh['history']))
            migrated = {'schema_version': '1.1', 'provider': 'tripo-cn',
                        'approval_sha256': authorization.approval_sha256, 'approval': deepcopy(approved),
                        'attempts': [], 'halted': False, 'legacy_root': root, 'legacy_history': fresh['history']}
            expected_bytes = (json.dumps(migrated, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode()
            after_sha = hashlib.sha256(expected_bytes).hexdigest()
            receipt = {'operation': 'apply_legacy_trial', 'status': 'prepared_for_cas', 'created_at': time.time(),
                       'ledger_file': fresh['ledger_file'], 'before_sha256': source['expected_sha256'],
                       'expected_after_sha256': after_sha, 'backup_descriptor': backup,
                       'review_descriptor': review, 'new_approval_sha256': authorization.approval_sha256,
                       'legacy_credits': 250, 'legacy_attempts': 6, 'network_calls': 0,
                       'rollback_condition': 'Manual only: no new attempt/accounting and exact expected_after_sha256; otherwise preserve all new finances and repair forward'}
            receipt_path = audit / ('migration-' + token + '.prepared.json')
            atomic_json(receipt_path, receipt)
            _require(digest(ledger_path) == source['expected_sha256'], 'Ledger changed before atomic migration')
            atomic_json(ledger_path, migrated)
            _require(digest(ledger_path) == after_sha, 'Migrated ledger changed after replacement; never restore stale backup')
            snapshot = BudgetLedger(ledger_path, authorization).snapshot()
            _require(snapshot['worst_or_accounted_credits'] == 250 and snapshot['attempts_used'] == 6,
                     'Migrated financial verification failed; preserve current state')
            receipt.update(status='applied', after_sha256=after_sha)
            completed = audit / ('migration-' + token + '.applied.json')
            atomic_json(completed, receipt)
            return {**receipt, 'receipt_file': str(completed.resolve())}
