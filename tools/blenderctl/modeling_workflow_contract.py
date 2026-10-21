"""Host validation and immutable inputs for the public modeling work unit."""
import copy
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from protocol import Failure, digest, read_json
from modeling_identity import implementation_sha256, runtime_documents


def normalize_params(value):
    from modeling_workflow_addon import contract
    if not isinstance(value, dict) or set(value) - {'manifest', 'implementation_sha256'} or 'manifest' not in value:
        raise Failure('INVALID_REQUEST', 'Modeling params require manifest and optional implementation_sha256')
    current = implementation_sha256()
    if value.get('implementation_sha256', current) != current:
        raise Failure('CONFLICT', 'Modeling implementation changed')
    try:
        manifest = contract.normalize_manifest(copy.deepcopy(value['manifest']))
    except contract.ContractError as exc:
        raise Failure('INVALID_REQUEST', str(exc)) from exc
    return {'manifest': manifest, 'implementation_sha256': current}


def input_documents(params):
    manifest = params['manifest']
    docs = runtime_documents()
    if manifest['operation'] == 'import_local':
        docs.append(manifest['input'])
    if manifest['operation'] in ('approval_append_prepare', 'approval_append_apply',
                                 'outstanding_increment_prepare', 'outstanding_increment_apply'):
        # The source ledger is intentionally mutable. Holding a host FileGuard
        # on it would deny the operation's own atomic replacement. The append
        # module owns its exact source SHA, evidence identities and CAS checks.
        docs.append(manifest['request_descriptor'])
        from modeling_workflow_addon.providers.approval_append import _read as guarded_append_read
        from modeling_workflow_addon.contract import normalize_provider_request
        request_value, _ = guarded_append_read(manifest['request_descriptor'])
        request = normalize_provider_request(request_value)
        for row in request.get('inputs', []):
            docs.append({'file': row['file'], 'expected_sha256': row['expected_sha256']})
    if manifest['operation'] == 'submit':
        for row in manifest.get('request', {}).get('inputs', []):
            docs.append({'file': row['file'], 'expected_sha256': row['expected_sha256']})
    if manifest['operation'] == 'resume_pre_submit':
        from modeling_workflow_addon.providers.pre_submit_recovery import TrustedPreSubmitRecovery
        from modeling_workflow_addon.contract import canonical_path
        statement = TrustedPreSubmitRecovery.from_descriptor(manifest['pre_submit_recovery_descriptor']).document
        docs.append(manifest['pre_submit_recovery_descriptor'])
        docs.append(statement['verification']['evidence_descriptor'])
        for row in manifest['request']['inputs']:
            docs.append({'file': row['file'], 'expected_sha256': row['expected_sha256']})
        # Ledger remains mutable; reject path aliases before the host guards it.
        if any(canonical_path(row['file']) == canonical_path(manifest['ledger_file'])
               for row in [*docs, manifest['authorization_descriptor']]):
            raise Failure('CONFLICT', 'Immutable pre-submit recovery evidence aliases mutable ledger')
    if manifest['operation'] == 'submit_batch':
        for request in manifest['requests']:
            for row in request.get('inputs', []):
                docs.append({'file': row['file'], 'expected_sha256': row['expected_sha256']})
    if manifest['operation'] == 'bind_unknown':
        from modeling_workflow_addon.providers.budget import TrustedBinding
        statement = TrustedBinding.from_descriptor(manifest['binding_descriptor']).document
        docs.append(manifest['binding_descriptor'])
        docs.append(statement['verification']['evidence_descriptor'])
    if manifest['operation'] == 'resume_download':
        docs.extend((manifest['recovery_descriptor'], manifest['partial_descriptor']))
    if manifest['operation'] == 'download_batch':
        for item in manifest['items']:
            if item['operation'] == 'resume_download':
                docs.extend((item['recovery_descriptor'], item['partial_descriptor']))
    if manifest.get('authorization_descriptor'):
        docs.append(manifest['authorization_descriptor'])
    if manifest.get('provider') == 'tripo-overseas' and 'ledger_file' in manifest:
        from modeling_workflow_addon.contract import canonical_path
        ledger_path = canonical_path(manifest['ledger_file'])
        if any(canonical_path(row['file']) == ledger_path for row in docs):
            raise Failure('CONFLICT', 'Immutable overseas input aliases mutable ledger')
    return docs


def schemas():
    from modeling_workflow_addon import contract
    domains = contract.schemas()
    manifest = domains['manifest']
    return {**domains, 'run': {'type': 'object', 'additionalProperties': False,
                    'required': ['manifest'], 'properties': {'manifest': manifest,
                    'implementation_sha256': {'type': 'string', 'pattern': '^[0-9a-f]{64}$'}}}}
