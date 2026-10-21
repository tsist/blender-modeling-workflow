"""Strict modeling manifests. Validation does not establish human authorization."""
import copy
import hashlib
import json
import os
from pathlib import Path
import re
from types import MappingProxyType


class ContractError(ValueError):
    pass


def obj(value, allowed, required=()):
    if not isinstance(value, dict) or set(value) - set(allowed) or set(required) - set(value):
        raise ContractError('Missing or unsupported contract fields')
    return value


def identity(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,256}', value):
        raise ContractError('Expected a bounded opaque identity')
    if value.startswith(('Bearer', 'tsk_', 'tcli_', 'file_')):
        raise ContractError('Credential/token cannot be used as identity')
    return value


def sha(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{64}', value):
        raise ContractError('Expected lowercase SHA-256')
    return value


def integer(value, low, high):
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ContractError('Integer outside contract bounds')
    return value


def path(value):
    if not isinstance(value, str) or not Path(value).is_absolute():
        raise ContractError('Expected an absolute path')
    return str(Path(value).resolve())


def canonical_path(value):
    """Windows paths compare case-insensitively after resolving aliases/dots."""
    return os.path.normcase(path(value))


def file_descriptor(value):
    obj(value, ('file', 'expected_sha256'), ('file', 'expected_sha256'))
    return {'file': path(value['file']), 'expected_sha256': sha(value['expected_sha256'])}


def credential_reference(value):
    if not isinstance(value, dict):
        raise ContractError('Credential must be an environment/DPAPI reference')
    if value.get('kind') == 'environment':
        obj(value, ('kind', 'name'), ('kind', 'name'))
        if not isinstance(value['name'], str) or not re.fullmatch(r'[A-Z][A-Z0-9_]{0,127}', value['name']):
            raise ContractError('Invalid environment variable name')
        return dict(value)
    if value.get('kind') in ('dpapi', 'dpapi_clixml'):
        obj(value, ('kind', 'path'), ('kind', 'path'))
        return {'kind': value['kind'], 'path': path(value['path'])}
    raise ContractError('Unsupported credential reference')


OPERATIONS = {'h31_image', 'h31_multiview', 'p2_multiview', 'segment_v2', 'texture_v35', 'complete_all_v1'}
H31_PARTS_OPERATION = 'h31_parts_multiview'
PROVIDER_OPERATIONS = OPERATIONS | {H31_PARTS_OPERATION}
H31_PARTS_PROVIDER = 'tripo-cn'
H31_PARTS_MODEL = 'v3.1-20260211'
H31_PARTS_WIRE_OPTIONS = MappingProxyType({'texture': False, 'pbr': False, 'generate_parts': True})
H31_PARTS_HASH_DOMAIN = 'modeling-provider-request/h31-native-parts/1.0'
MESHY_OPERATIONS = ('image_to_3d','multi_image_to_3d','text_to_3d','retexture','remesh')


def _normalize_provider_request_v1(value):
    obj(value, ('operation_id', 'operation', 'inputs', 'source_task_id'), ('operation_id', 'operation'))
    out = {'operation_id': identity(value['operation_id']), 'operation': value['operation']}
    if out['operation'] not in OPERATIONS:
        raise ContractError('Operation is outside verified provider capability')
    if out['operation'] in {'h31_image', 'h31_multiview', 'p2_multiview'}:
        if 'source_task_id' in value:
            raise ContractError('Generation does not accept source task')
        inputs = value.get('inputs')
        views = ['front'] if out['operation'] == 'h31_image' else ['front', 'back']
        if not isinstance(inputs, list) or len(inputs) != len(views):
            raise ContractError('Verified generation requires front or front/back PNG inputs')
        out['inputs'] = []
        for row, view in zip(inputs, views):
            obj(row, ('view', 'file', 'expected_sha256'), ('view', 'file', 'expected_sha256'))
            if row['view'] != view:
                raise ContractError('Inputs must preserve front/back order')
            out['inputs'].append({'view': view, **file_descriptor({k: row[k] for k in ('file', 'expected_sha256')})})
    else:
        if 'inputs' in value:
            raise ContractError('Postprocessing requires a supported source task, not image inputs')
        out['source_task_id'] = identity(value.get('source_task_id'))
    return out


def normalize_provider_request(value):
    """Old six requests retain their exact shape; parts has a fixed new contract."""
    if not isinstance(value, dict) or value.get('operation') != H31_PARTS_OPERATION:
        return _normalize_provider_request_v1(value)
    obj(value, ('operation_id', 'operation', 'inputs'), ('operation_id', 'operation', 'inputs'))
    inputs = value['inputs']
    if not isinstance(inputs, list) or len(inputs) != 2:
        raise ContractError('Native parts requires front/back PNG inputs')
    out = {'operation_id': identity(value['operation_id']), 'operation': H31_PARTS_OPERATION, 'inputs': []}
    for row, view in zip(inputs, ('front', 'back')):
        obj(row, ('view', 'file', 'expected_sha256'), ('view', 'file', 'expected_sha256'))
        if row['view'] != view:
            raise ContractError('Inputs must preserve front/back order')
        descriptor = file_descriptor({key: row[key] for key in ('file', 'expected_sha256')})
        if Path(descriptor['file']).suffix.lower() != '.png':
            raise ContractError('Native parts input must be a PNG file')
        out['inputs'].append({'view': view, **descriptor})
    return out


def request_sha256(value):
    normalized = normalize_provider_request(value)
    if normalized['operation'] == H31_PARTS_OPERATION:
        normalized = {'request_contract_version': H31_PARTS_HASH_DOMAIN,
                      'provider': H31_PARTS_PROVIDER, 'model': H31_PARTS_MODEL,
                      'wire_options': dict(H31_PARTS_WIRE_OPTIONS), 'request': normalized}
    return hashlib.sha256(json.dumps(normalized, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')).hexdigest()


def normalize_binding_document(value):
    """A caller's reviewed mapping statement; hashes do not authenticate humans."""
    fields = ('schema_version', 'approved', 'purpose', 'provider', 'base_url', 'ledger_file',
              'approval_sha256', 'expected_ledger_sha256', 'attempt_id', 'operation_id',
              'operation', 'request_sha256', 'task_id', 'verification')
    obj(value, fields, fields)
    if (value['schema_version'] != '1.0' or value['approved'] is not True
            or value['purpose'] != 'bind_unknown_submission' or value['provider'] != 'tripo-cn'
            or value['base_url'] != 'https://openapi.tripo3d.com/v3'):
        raise ContractError('Explicit reviewed China task binding statement required')
    out = {key: value[key] for key in ('schema_version', 'approved', 'purpose', 'provider', 'base_url')}
    out['ledger_file'] = canonical_path(value['ledger_file'])
    for key in ('approval_sha256', 'expected_ledger_sha256', 'request_sha256'):
        out[key] = sha(value[key])
    for key in ('attempt_id', 'operation_id', 'task_id'):
        out[key] = identity(value[key])
    if value['operation'] not in PROVIDER_OPERATIONS:
        raise ContractError('Binding operation outside verified provider scope')
    out['operation'] = value['operation']
    verification = value['verification']
    obj(verification, ('method', 'verified_request_match', 'evidence_descriptor'),
        ('method', 'verified_request_match', 'evidence_descriptor'))
    if verification['method'] not in ('provider_console', 'recovered_acceptance_receipt') or verification['verified_request_match'] is not True:
        raise ContractError('Operator must have verified the original request-to-task mapping')
    out['verification'] = {'method': verification['method'], 'verified_request_match': True,
                           'evidence_descriptor': file_descriptor(verification['evidence_descriptor'])}
    return out


def normalize_pre_submit_recovery_document(value):
    """Caller-reviewed prior-owner proof and new recovery approval, not inferred consent.

    The caller verifies the original job/owner and prior source implementation.
    A SHA provides integrity only; the library cannot authenticate a human or
    infer that generation never started from elapsed time or a reserved row.
    """
    fields = ('schema_version', 'approved', 'purpose', 'provider', 'base_url', 'ledger_file',
              'approval_sha256', 'expected_ledger_sha256', 'attempt_id', 'operation_id',
              'operation', 'request_sha256', 'prior_implementation_sha256', 'verification')
    if isinstance(value, dict) and value.get('schema_version') == '1.1':
        fields += ('ledger_schema_version',)
    obj(value, fields, fields)
    if (value['schema_version'] not in ('1.0', '1.1') or value['approved'] is not True
            or value['purpose'] != 'resume_pre_submit' or value['provider'] != 'tripo-cn'
            or value['base_url'] != 'https://openapi.tripo3d.com/v3'):
        raise ContractError('Explicit reviewed China pre-submit recovery approval required')
    out = {key: value[key] for key in ('schema_version', 'approved', 'purpose', 'provider', 'base_url')}
    if value['schema_version'] == '1.1':
        if value['ledger_schema_version'] != '1.2':
            raise ContractError('Recovery statement 1.1 requires an explicit schema-1.2 ledger')
        out['ledger_schema_version'] = '1.2'
    out['ledger_file'] = canonical_path(value['ledger_file'])
    for key in ('approval_sha256', 'expected_ledger_sha256', 'request_sha256', 'prior_implementation_sha256'):
        out[key] = sha(value[key])
    for key in ('attempt_id', 'operation_id'):
        out[key] = identity(value[key])
    if value['operation'] not in ('h31_image', 'h31_multiview', 'p2_multiview', H31_PARTS_OPERATION):
        raise ContractError('Pre-submit recovery only permits original generation operations')
    out['operation'] = value['operation']
    verification = value['verification']
    fields = ('owner_stopped', 'generation_post_not_started', 'evidence_descriptor')
    obj(verification, fields, fields)
    if verification['owner_stopped'] is not True or verification['generation_post_not_started'] is not True:
        raise ContractError('Caller must verify stopped original owner and generation POST not started')
    out['verification'] = {'owner_stopped': True, 'generation_post_not_started': True,
                           'evidence_descriptor': file_descriptor(verification['evidence_descriptor'])}
    return out


def _normalize_manifest_v1(value):
    obj(value, ('schema_version', 'operation', 'input', 'provenance', 'preview',
                'credential_ref', 'ledger_file', 'task_id', 'task_ids', 'request', 'authorization_descriptor', 'binding_descriptor',
                'max_bytes', 'recovery_descriptor', 'partial_descriptor', 'requests', 'query_limit',
                'poll_interval_seconds', 'items', 'max_total_bytes'),
        ('schema_version', 'operation'))
    if value['schema_version'] != '1.0':
        raise ContractError('Expected modeling manifest schema 1.0')
    operation = value['operation']
    if operation == 'import_local':
        obj(value, ('schema_version', 'operation', 'input', 'provenance', 'preview'), ('input',))
        out = {'schema_version': '1.0', 'operation': operation, 'input': file_descriptor(value['input'])}
        provenance = value.get('provenance', {'provider': 'local'})
        obj(provenance, ('provider', 'task_id', 'model'), ('provider',))
        if provenance['provider'] not in ('tripo-cn', 'local'):
            raise ContractError('Unsupported provenance provider')
        out['provenance'] = {'provider': provenance['provider']}
        for key in ('task_id', 'model'):
            if key in provenance:
                out['provenance'][key] = identity(provenance[key])
        preview = value.get('preview', {})
        obj(preview, ('enabled', 'front_axis', 'engine', 'width', 'height', 'samples', 'views'))
        settings = {'enabled': True, 'front_axis': '+Y', 'engine': 'BLENDER_WORKBENCH',
                    'width': 768, 'height': 768, 'samples': 16, 'views': ['front', 'back', 'threequarter']}
        settings.update(copy.deepcopy(preview))
        if type(settings['enabled']) is not bool or settings['front_axis'] not in ('+X', '-X', '+Y', '-Y') or settings['engine'] not in ('CYCLES', 'BLENDER_WORKBENCH'):
            raise ContractError('Invalid preview configuration')
        for key in ('width', 'height'):
            integer(settings[key], 64, 2048)
        integer(settings['samples'], 1, 128)
        views = settings['views']
        if not isinstance(views, list) or not 1 <= len(views) <= 4 or len(set(views)) != len(views) or any(v not in ('front', 'back', 'threequarter', 'side') for v in views):
            raise ContractError('Invalid preview views')
        out['preview'] = settings
        return out
    if operation not in ('submit', 'submit_batch', 'query', 'query_batch', 'download', 'download_batch', 'resume_download', 'bind_unknown'):
        raise ContractError('Unsupported modeling manifest operation')
    allowed = {'schema_version', 'operation', 'credential_ref', 'ledger_file'}
    required = allowed.copy()
    if operation == 'submit':
        allowed.update(('request', 'authorization_descriptor')); required.update(('request', 'authorization_descriptor'))
    elif operation == 'submit_batch':
        allowed.update(('requests', 'authorization_descriptor', 'query_limit', 'poll_interval_seconds'))
        required.update(('requests', 'authorization_descriptor', 'query_limit', 'poll_interval_seconds'))
    elif operation == 'download_batch':
        allowed.update(('items', 'max_total_bytes')); required.update(('items', 'max_total_bytes'))
    elif operation == 'bind_unknown':
        allowed.update(('request', 'binding_descriptor')); required.update(('request', 'binding_descriptor'))
    elif operation == 'query_batch':
        allowed.add('task_ids'); required.add('task_ids')
    else:
        allowed.add('task_id'); required.add('task_id')
        if operation in ('download', 'resume_download'):
            allowed.add('max_bytes')
        if operation == 'resume_download':
            allowed.update(('recovery_descriptor', 'partial_descriptor'))
            required.update(('recovery_descriptor', 'partial_descriptor'))
    obj(value, allowed, required)
    out = {'schema_version': '1.0', 'operation': operation,
           'credential_ref': credential_reference(value['credential_ref']), 'ledger_file': canonical_path(value['ledger_file'])}
    if operation == 'submit':
        out['request'] = _normalize_provider_request_v1(value['request'])
        out['authorization_descriptor'] = file_descriptor(value['authorization_descriptor'])
    elif operation == 'submit_batch':
        items = value['requests']
        if not isinstance(items, list) or not 1 <= len(items) <= 8:
            raise ContractError('Submit batch requires 1 to 8 approved requests')
        out['requests'] = [_normalize_provider_request_v1(item) for item in items]
        if len({item['operation_id'] for item in out['requests']}) != len(items):
            raise ContractError('Submit batch operation identities must be unique')
        out['authorization_descriptor'] = file_descriptor(value['authorization_descriptor'])
        out['query_limit'] = integer(value['query_limit'], 1, 120)
        out['poll_interval_seconds'] = integer(value['poll_interval_seconds'], 1, 30)
    elif operation == 'download_batch':
        items = value['items']
        if not isinstance(items, list) or not 1 <= len(items) <= 16:
            raise ContractError('Download batch requires 1 to 16 known task items')
        out['items'] = []
        for item in items:
            obj(item, ('operation', 'task_id', 'max_bytes', 'recovery_descriptor', 'partial_descriptor'),
                ('operation', 'task_id', 'max_bytes'))
            if item['operation'] not in ('download', 'resume_download'):
                raise ContractError('Download batch only accepts known-task downloads')
            normalized = normalize_manifest({**item, 'schema_version': '1.0',
                'credential_ref': out['credential_ref'], 'ledger_file': out['ledger_file']})
            out['items'].append({key: item_value for key, item_value in normalized.items()
                                 if key not in ('schema_version', 'credential_ref', 'ledger_file')})
        if len({item['task_id'] for item in out['items']}) != len(items):
            raise ContractError('Download batch task identities must be unique')
        out['max_total_bytes'] = integer(value['max_total_bytes'], 12, 32 * 1024 ** 3)
        if sum(item['max_bytes'] for item in out['items']) > out['max_total_bytes']:
            raise ContractError('Download batch worst-case outputs exceed the total byte bound')
    elif operation == 'bind_unknown':
        out['request'] = _normalize_provider_request_v1(value['request'])
        out['binding_descriptor'] = file_descriptor(value['binding_descriptor'])
    elif operation == 'query_batch':
        items = value['task_ids']
        if not isinstance(items, list) or not 1 <= len(items) <= 16:
            raise ContractError('Query batch requires 1 to 16 task identities')
        out['task_ids'] = [identity(item) for item in items]
        if len(set(out['task_ids'])) != len(items):
            raise ContractError('Query batch task identities must be unique')
    else:
        out['task_id'] = identity(value['task_id'])
        if operation in ('download', 'resume_download'):
            out['max_bytes'] = integer(value.get('max_bytes', 2 * 1024 ** 3), 12, 2 * 1024 ** 3)
        if operation == 'resume_download':
            for key in ('recovery_descriptor', 'partial_descriptor'):
                out[key] = file_descriptor(value[key])
    return out


def _schemas_v1():
    """Discovery schemas; normalize_manifest remains authoritative validation."""
    descriptor = {'type': 'object', 'additionalProperties': False,
                  'required': ['file', 'expected_sha256'], 'properties': {
                      'file': {'type': 'string', 'description': 'Absolute file path'},
                      'expected_sha256': {'type': 'string', 'pattern': '^[0-9a-f]{64}$'}}}
    reference = {'oneOf': [
        {'type': 'object', 'additionalProperties': False, 'required': ['kind', 'name'],
         'properties': {'kind': {'const': 'environment'}, 'name': {'type': 'string', 'pattern': '^[A-Z][A-Z0-9_]{0,127}$'}}},
        {'type': 'object', 'additionalProperties': False, 'required': ['kind', 'path'],
         'properties': {'kind': {'enum': ['dpapi', 'dpapi_clixml']}, 'path': {'type': 'string', 'description': 'Absolute DPAPI encrypted-file or SecureString CLIXML path'}}}]}
    request = {'type': 'object', 'additionalProperties': False, 'required': ['operation_id', 'operation'],
        'properties': {'operation_id': {'type': 'string', 'pattern': '^[A-Za-z0-9_.-]{1,256}$'},
                       'operation': {'enum': sorted(OPERATIONS)}, 'source_task_id': {'type': 'string'},
                       'inputs': {'type': 'array', 'minItems': 1, 'maxItems': 2, 'items': {
                           'type': 'object', 'additionalProperties': False, 'required': ['view', 'file', 'expected_sha256'],
                           'properties': {'view': {'enum': ['front', 'back']}, **descriptor['properties']}}}},
        'description': 'Generation requires ordered front or front/back inputs; segment/texture require successful original generation, complete_all_v1 requires successful segment_v2; every source_task_id must be in the same ledger'}
    result = {}
    actions = ('import_local', 'submit', 'submit_batch', 'query', 'query_batch', 'download', 'download_batch', 'resume_download', 'bind_unknown')
    for action in actions:
        props = {'schema_version': {'const': '1.0'}, 'operation': {'const': action}}
        required = ['schema_version', 'operation']
        if action == 'import_local':
            props.update(input=descriptor, provenance={'type': 'object', 'additionalProperties': False,
                'required': ['provider'], 'properties': {'provider': {'enum': ['tripo-cn', 'local']},
                'task_id': {'type': 'string'}, 'model': {'type': 'string'}}},
                preview={'type': 'object', 'additionalProperties': False, 'properties': {
                    'enabled': {'type': 'boolean'}, 'front_axis': {'enum': ['+X', '-X', '+Y', '-Y']},
                    'engine': {'enum': ['CYCLES', 'BLENDER_WORKBENCH']},
                    'width': {'type': 'integer', 'minimum': 64, 'maximum': 2048},
                    'height': {'type': 'integer', 'minimum': 64, 'maximum': 2048},
                    'samples': {'type': 'integer', 'minimum': 1, 'maximum': 128},
                    'views': {'type': 'array', 'minItems': 1, 'maxItems': 4, 'uniqueItems': True,
                              'items': {'enum': ['front', 'back', 'threequarter', 'side']}}}})
            required.append('input')
        else:
            props.update(credential_ref=reference, ledger_file={'type': 'string', 'description': 'Absolute canonical financial ledger path, matching authorization.ledger_file for submit; external recovery does not create it'})
            required += ['credential_ref', 'ledger_file']
            if action == 'submit':
                props.update(request=request, authorization_descriptor=descriptor)
                required += ['request', 'authorization_descriptor']
            elif action == 'submit_batch':
                props.update(requests={'type': 'array', 'minItems': 1, 'maxItems': 8, 'items': request},
                    authorization_descriptor=descriptor,
                    query_limit={'type': 'integer', 'minimum': 1, 'maximum': 120,
                        'description': 'Total task GET attempts across this explicit job; balance GET is separate'},
                    poll_interval_seconds={'type': 'integer', 'minimum': 1, 'maximum': 30})
                required += ['requests', 'authorization_descriptor', 'query_limit', 'poll_interval_seconds']
            elif action == 'download_batch':
                basic = {'type': 'object', 'additionalProperties': False,
                    'required': ['operation', 'task_id', 'max_bytes'], 'properties': {
                        'operation': {'const': 'download'}, 'task_id': {'type': 'string'},
                        'max_bytes': {'type': 'integer', 'minimum': 12, 'maximum': 2 * 1024 ** 3}}}
                resume = copy.deepcopy(basic)
                resume['properties']['operation'] = {'const': 'resume_download'}
                resume['properties'].update(recovery_descriptor=descriptor, partial_descriptor=descriptor)
                resume['required'] += ['recovery_descriptor', 'partial_descriptor']
                props.update(items={'type': 'array', 'minItems': 1, 'maxItems': 16,
                                    'items': {'oneOf': [basic, resume]}},
                    max_total_bytes={'type': 'integer', 'minimum': 12, 'maximum': 32 * 1024 ** 3,
                                     'description': 'Must cover the sum of all item max_bytes; no automatic full redownload'})
                required += ['items', 'max_total_bytes']
            elif action == 'bind_unknown':
                props.update(request=request, binding_descriptor=descriptor)
                required += ['request', 'binding_descriptor']
            elif action == 'query_batch':
                props['task_ids'] = {'type': 'array', 'minItems': 1, 'maxItems': 16, 'uniqueItems': True,
                    'items': {'type': 'string', 'pattern': '^[A-Za-z0-9_.-]{1,256}$'},
                    'description': 'One sequential GET per known task; no polling or automatic retry; first query error stops the batch'}
                required.append('task_ids')
            else:
                props['task_id'] = {'type': 'string'}; required.append('task_id')
                if action in ('download', 'resume_download'):
                    props['max_bytes'] = {'type': 'integer', 'minimum': 12, 'maximum': 2 * 1024 ** 3}
                if action == 'resume_download':
                    props.update(recovery_descriptor=descriptor, partial_descriptor=descriptor)
                    required += ['recovery_descriptor', 'partial_descriptor']
        result[action] = {'type': 'object', 'additionalProperties': False, 'required': required, 'properties': props}
    result['manifest'] = {'oneOf': [result[key] for key in actions]}
    result['authorization'] = {'type': 'object', 'additionalProperties': False,
        'required': ['schema_version', 'approved', 'approval_id', 'provider', 'base_url', 'ledger_file',
                     'total_cap_credits', 'max_attempts', 'allows_upload', 'operations'],
        'properties': {
            'schema_version': {'const': '1.0'}, 'approved': {'const': True},
            'approval_id': {'type': 'string', 'pattern': '^[A-Za-z0-9_.-]{1,256}$'},
            'provider': {'const': 'tripo-cn'}, 'base_url': {'const': 'https://openapi.tripo3d.com/v3'},
            'ledger_file': {'type': 'string', 'description': 'Unique absolute canonical ledger path bound to this approval'},
            'total_cap_credits': {'type': 'integer', 'minimum': 1, 'maximum': 1000000000},
            'max_attempts': {'type': 'integer', 'minimum': 1, 'maximum': 10000},
            'allows_upload': {'type': 'boolean'},
            'operations': {'type': 'array', 'minItems': 1, 'maxItems': 10000, 'items': {
                'type': 'object', 'additionalProperties': False,
                'required': ['operation_id', 'operation', 'request_sha256', 'max_credits'],
                'properties': {'operation_id': {'type': 'string', 'pattern': '^[A-Za-z0-9_.-]{1,256}$'},
                               'operation': {'enum': sorted(PROVIDER_OPERATIONS)},
                               'request_sha256': {'type': 'string', 'pattern': '^[0-9a-f]{64}$'},
                               'max_credits': {'type': 'integer', 'minimum': 1, 'maximum': 1000000000}}}}}}
    binding_fields = ['schema_version', 'approved', 'purpose', 'provider', 'base_url', 'ledger_file',
                      'approval_sha256', 'expected_ledger_sha256', 'attempt_id', 'operation_id',
                      'operation', 'request_sha256', 'task_id', 'verification']
    result['binding_statement'] = {'type': 'object', 'additionalProperties': False,
        'required': binding_fields, 'properties': {
            'schema_version': {'const': '1.0'}, 'approved': {'const': True},
            'purpose': {'const': 'bind_unknown_submission'}, 'provider': {'const': 'tripo-cn'},
            'base_url': {'const': 'https://openapi.tripo3d.com/v3'},
            'ledger_file': {'type': 'string', 'description': 'Existing approval-bound canonical ledger path'},
            **{key: {'type': 'string', 'pattern': '^[0-9a-f]{64}$'} for key in ('approval_sha256', 'expected_ledger_sha256', 'request_sha256')},
            **{key: {'type': 'string', 'pattern': '^[A-Za-z0-9_.-]{1,256}$'} for key in ('attempt_id', 'operation_id', 'task_id')},
            'operation': {'enum': sorted(PROVIDER_OPERATIONS)},
            'verification': {'type': 'object', 'additionalProperties': False,
                'required': ['method', 'verified_request_match', 'evidence_descriptor'],
                'properties': {'method': {'enum': ['provider_console', 'recovered_acceptance_receipt']},
                               'verified_request_match': {'const': True}, 'evidence_descriptor': descriptor}}}}
    return result


def normalize_manifest(value):
    """v1 stays byte-compatible; v1.1 explicitly scopes provider/task identity."""
    if not isinstance(value, dict) or value.get('schema_version') != '1.1':
        return _normalize_manifest_v1(value)
    provider = value.get('provider')
    if provider == 'tripo-overseas' and value.get('operation') != 'import_local':
        from .providers.overseas_contract import normalize_manifest as normalize_overseas
        return normalize_overseas(value)
    if value.get('operation') == 'resume_pre_submit':
        fields = ('schema_version', 'provider', 'operation', 'credential_ref', 'ledger_file',
                  'request', 'authorization_descriptor', 'pre_submit_recovery_descriptor')
        obj(value, fields, fields)
        if provider != 'tripo-cn':
            raise ContractError('Pre-submit recovery requires manifest 1.1 and explicit tripo-cn')
        request = normalize_provider_request(value['request'])
        if request['operation'] not in ('h31_image', 'h31_multiview', 'p2_multiview', H31_PARTS_OPERATION):
            raise ContractError('Pre-submit recovery only permits generation')
        return {'schema_version': '1.1', 'provider': 'tripo-cn', 'operation': 'resume_pre_submit',
                'credential_ref': credential_reference(value['credential_ref']),
                'ledger_file': canonical_path(value['ledger_file']), 'request': request,
                'authorization_descriptor': file_descriptor(value['authorization_descriptor']),
                'pre_submit_recovery_descriptor': file_descriptor(value['pre_submit_recovery_descriptor'])}
    if value.get('operation') in ('approval_append_prepare', 'approval_append_apply',
                                  'outstanding_increment_prepare', 'outstanding_increment_apply'):
        if provider != 'tripo-cn':
            raise ContractError('Approval append is local to the Tripo China ledger; no provider fallback')
        fields = ['schema_version', 'provider', 'operation', 'source_descriptor', 'request_descriptor']
        if value['operation'] in ('approval_append_apply', 'outstanding_increment_apply'):
            fields.append('authorization_descriptor')
        obj(value, fields, fields)
        out = {key: value[key] for key in ('schema_version', 'provider', 'operation')}
        for key in fields[3:]:
            out[key] = file_descriptor(value[key])
        source_path = canonical_path(out['source_descriptor']['file'])
        if any(canonical_path(out[key]['file']) == source_path for key in fields[4:]):
            raise ContractError('Immutable approval append evidence aliases the mutable ledger')
        return out
    if value.get('operation') == 'import_local':
        obj(value, ('schema_version','operation','provider','input','provenance','preview'),
            ('schema_version','operation','provider','input'))
        if provider not in ('local','tripo-cn','meshy','tripo-overseas'):
            raise ContractError('Unsupported import provenance provider')
        provenance = value.get('provenance', {'provider':provider})
        obj(provenance, ('provider','task_id','model','remote_operation','adapter_version'), ('provider',))
        if provenance['provider'] != provider:
            raise ContractError('Import provenance must match explicit provider')
        actual = {'provider':provider}
        for key in ('task_id','model','remote_operation','adapter_version'):
            if key in provenance:
                actual[key] = identity(provenance[key])
        if provider == 'tripo-overseas' and 'task_id' in actual:
            from .providers.overseas_contract import task_id as overseas_task_id
            actual['task_id'] = overseas_task_id(actual['task_id'])
        if provider == 'meshy' and 'task_id' in actual:
            if actual.get('remote_operation') not in MESHY_OPERATIONS:
                raise ContractError('Meshy task provenance requires its endpoint operation')
        legacy = {k:v for k,v in value.items() if k not in ('provider','provenance')}
        legacy.update(schema_version='1.0',provenance={'provider':'local'})
        out = _normalize_manifest_v1(legacy)
        out.update(schema_version='1.1',provider=provider,provenance=actual)
        return out
    if provider == 'tripo-cn':
        requests = value.get('requests', []) if value.get('operation') == 'submit_batch' else [value.get('request')]
        has_parts = isinstance(requests, list) and any(
            isinstance(request, dict) and request.get('operation') == H31_PARTS_OPERATION
            for request in requests)
        if has_parts:
            return _normalize_parts_manifest_v11(value)
        legacy = {k:v for k,v in value.items() if k != 'provider'}
        legacy['schema_version'] = '1.0'
        out = _normalize_manifest_v1(legacy)
        out.update(schema_version='1.1',provider=provider)
        return out
    if provider != 'meshy':
        raise ContractError('Provider is not registered; no implicit fallback')
    fields = ('schema_version','provider','operation','remote_operation','task_id','credential_ref')
    obj(value, fields, fields)
    if value['operation'] != 'query' or not isinstance(value['remote_operation'], str) or value['remote_operation'] not in MESHY_OPERATIONS:
        raise ContractError('Meshy supports explicit known-task query only; no implicit fallback')
    if value['task_id'] in ('.','..'):
        raise ContractError('Meshy task identity cannot be a URI dot segment')
    return {'schema_version':'1.1','provider':'meshy','operation':'query',
            'remote_operation':value['remote_operation'], 'task_id':identity(value['task_id']),
            'credential_ref':credential_reference(value['credential_ref'])}


def _normalize_parts_manifest_v11(value):
    """Never pass new requests through the legacy manifest normalizer."""
    action = value.get('operation')
    common = ('schema_version', 'provider', 'operation', 'credential_ref', 'ledger_file')
    extra = {'submit': ('request', 'authorization_descriptor'),
             'bind_unknown': ('request', 'binding_descriptor'),
             'submit_batch': ('requests', 'authorization_descriptor', 'query_limit', 'poll_interval_seconds')}
    if action not in extra:
        raise ContractError('Native parts requires an explicit submit, submit_batch or bind_unknown manifest')
    fields = common + extra[action]
    obj(value, fields, fields)
    if value['schema_version'] != '1.1' or value['provider'] != H31_PARTS_PROVIDER:
        raise ContractError('Native parts requires manifest 1.1 and provider tripo-cn')
    out = {'schema_version': '1.1', 'provider': H31_PARTS_PROVIDER, 'operation': action,
           'credential_ref': credential_reference(value['credential_ref']),
           'ledger_file': canonical_path(value['ledger_file'])}
    if action == 'submit_batch':
        requests = value['requests']
        if not isinstance(requests, list) or not 1 <= len(requests) <= 8:
            raise ContractError('Submit batch requires 1 to 8 approved requests')
        out['requests'] = [normalize_provider_request(request) for request in requests]
        if len({request['operation_id'] for request in out['requests']}) != len(requests):
            raise ContractError('Submit batch operation identities must be unique')
        out['query_limit'] = integer(value['query_limit'], 1, 120)
        out['poll_interval_seconds'] = integer(value['poll_interval_seconds'], 1, 30)
    else:
        out['request'] = normalize_provider_request(value['request'])
    descriptor_key = 'binding_descriptor' if action == 'bind_unknown' else 'authorization_descriptor'
    out[descriptor_key] = file_descriptor(value[descriptor_key])
    return out


def h31_parts_request_schema():
    """Exact offline candidate scope, with no seed or arbitrary wire options."""
    item = {'type': 'object', 'additionalProperties': False,
            'required': ['view', 'file', 'expected_sha256'], 'properties': {
                'view': {'enum': ['front', 'back']},
                'file': {'type': 'string', 'pattern': '[.][pP][nN][gG]$',
                         'description': 'Absolute PNG input path; provider verifies bytes and SHA'},
                'expected_sha256': {'type': 'string', 'pattern': '^[0-9a-f]{64}$'}}}
    ordered = []
    for view in ('front', 'back'):
        row = copy.deepcopy(item)
        row['properties']['view'] = {'const': view}
        ordered.append(row)
    return {'type': 'object', 'additionalProperties': False,
            'required': ['operation_id', 'operation', 'inputs'], 'properties': {
                'operation_id': {'type': 'string', 'pattern': '^[A-Za-z0-9_.-]{1,256}$'},
                'operation': {'const': H31_PARTS_OPERATION},
                'inputs': {'type': 'array', 'minItems': 2, 'maxItems': 2,
                           'prefixItems': ordered, 'items': False}},
            'description': 'Manifest 1.1 Tripo China only. Fixed v3.1-20260211, texture=false, pbr=false, generate_parts=true; default standard geometry. H2 public submit/query/download/import and fresh reopen verified once on 2026-10-14 at 40 observed credits; semantic quality partial_fail and HD texture preservation not_run. One case does not guarantee arbitrary parts names, hierarchy or complete character quality.'}


def meshy_query_schema(remote_operation=None):
    reference = _schemas_v1()['query']['properties']['credential_ref']
    return {'type':'object', 'additionalProperties':False,
            'required':['schema_version','provider','operation','remote_operation','task_id','credential_ref'],
            'properties':{'schema_version':{'const':'1.1'}, 'provider':{'const':'meshy'},
                'operation':{'const':'query'}, 'remote_operation':{'const':remote_operation} if remote_operation else {'enum':list(MESHY_OPERATIONS)},
                'task_id':{'type':'string','pattern':'^(?!\\.{1,2}$)[A-Za-z0-9_.-]{1,256}$'}, 'credential_ref':reference}}


def schemas():
    result = _schemas_v1()
    explicit = []
    actions = ('import_local','submit','submit_batch','query','query_batch','download','download_batch','resume_download','bind_unknown')
    for action in actions:
        row = copy.deepcopy(result[action])
        row['properties']['schema_version'] = {'const':'1.1'}
        row['properties']['provider'] = {'enum':['local','tripo-cn','meshy','tripo-overseas']} if action == 'import_local' else {'const':'tripo-cn'}
        row['required'].append('provider')
        if action == 'import_local':
            row['properties']['provenance']['properties']['provider'] = {'enum':['local','tripo-cn','meshy','tripo-overseas']}
            row['properties']['provenance']['properties'].update({k:{'type':'string'} for k in ('remote_operation','adapter_version')})
        if action in ('submit', 'bind_unknown'):
            row['properties']['request'] = {'oneOf': [row['properties']['request'], h31_parts_request_schema()]}
        elif action == 'submit_batch':
            row['properties']['requests']['items'] = {'oneOf': [row['properties']['requests']['items'], h31_parts_request_schema()]}
        explicit.append(row)
    result['provider_query'] = meshy_query_schema()
    recovery = copy.deepcopy(result['submit'])
    recovery['properties']['schema_version'] = {'const': '1.1'}
    recovery['properties']['provider'] = {'const': 'tripo-cn'}
    recovery['properties']['operation'] = {'const': 'resume_pre_submit'}
    recovery['properties']['request'] = {'oneOf': [
        {**copy.deepcopy(result['submit']['properties']['request']),
         'properties': {**copy.deepcopy(result['submit']['properties']['request']['properties']),
                        'operation': {'enum': ['h31_image', 'h31_multiview', 'p2_multiview']}}},
        h31_parts_request_schema()]}
    recovery['properties']['pre_submit_recovery_descriptor'] = copy.deepcopy(recovery['properties']['authorization_descriptor'])
    recovery['required'] += ['provider', 'pre_submit_recovery_descriptor']
    recovery['description'] = 'Explicit new caller-reviewed recovery approval and original authorization; statement 1.0 scopes ordinary ledger1.0, statement1.1 explicitly scopes the sole added ledger1.2 reservation. Frozen history never changes; one claim, no automatic retry. SHA is integrity, not human authentication.'
    result['resume_pre_submit'] = recovery
    explicit.append(recovery)
    statement = copy.deepcopy(result['binding_statement'])
    props = statement['properties']
    props.pop('task_id')
    props['purpose'] = {'const': 'resume_pre_submit'}
    props['operation'] = {'enum': ['h31_image', 'h31_multiview', 'p2_multiview', H31_PARTS_OPERATION]}
    props['prior_implementation_sha256'] = {'type': 'string', 'pattern': '^[0-9a-f]{64}$'}
    props['verification'] = {'type': 'object', 'additionalProperties': False,
        'required': ['owner_stopped', 'generation_post_not_started', 'evidence_descriptor'],
        'properties': {'owner_stopped': {'const': True}, 'generation_post_not_started': {'const': True},
                       'evidence_descriptor': copy.deepcopy(recovery['properties']['authorization_descriptor'])}}
    statement['required'] = [key for key in statement['required'] if key != 'task_id'] + ['prior_implementation_sha256']
    statement['description'] = 'Caller verifies original stopped job/owner, original implementation and generation POST never started, plus fresh human recovery approval. SHA is integrity, not authentication.'
    props['schema_version'] = {'enum': ['1.0', '1.1']}
    props['ledger_schema_version'] = {'const': '1.2',
        'description': 'Required only for statement 1.1; scopes recovery to the single increment attempt, never frozen history'}
    statement['oneOf'] = [
        {'properties': {'schema_version': {'const': '1.0'}},
         'not': {'required': ['ledger_schema_version']}},
        {'properties': {'schema_version': {'const': '1.1'}},
         'required': ['ledger_schema_version']}]
    result['pre_submit_recovery_statement'] = statement
    for action in ('approval_append_prepare', 'approval_append_apply',
                   'outstanding_increment_prepare', 'outstanding_increment_apply'):
        descriptor = {'type': 'object', 'additionalProperties': False,
                      'required': ['file', 'expected_sha256'], 'properties': {
                          'file': {'type': 'string', 'description': 'Absolute local file path'},
                          'expected_sha256': {'type': 'string', 'pattern': '^[0-9a-f]{64}$'}}}
        props = {'schema_version': {'const': '1.1'}, 'provider': {'const': 'tripo-cn'},
                 'operation': {'const': action},
                 'source_descriptor': copy.deepcopy(descriptor),
                 'request_descriptor': copy.deepcopy(descriptor)}
        if action in ('approval_append_apply', 'outstanding_increment_apply'):
            props['authorization_descriptor'] = copy.deepcopy(descriptor)
        row = {'type': 'object', 'additionalProperties': False,
               'required': list(props), 'properties': props,
               'description': 'Local same-ledger approval append; no credentials or network. Source ledger is mutable and guarded by the operation CAS.'}
        if action.startswith('outstanding_increment_'):
            row['description'] = ('Explicit local 20-credit/one-attempt increment from ordinary ledger1.0 to ledger1.2; '
                                  'freeze the original unknown reservation and all history, bind exactly one new generation request. '
                                  'No credentials or network; source ledger is mutable and guarded by CAS.')
        result[action] = row
        explicit.append(row)
    from .providers.overseas_contract import schemas as overseas_schemas
    overseas = overseas_schemas()
    result.update(overseas)
    result['manifest']['oneOf'] += explicit + [result['provider_query']] + [
        overseas['overseas_' + action] for action in ('submit', 'query', 'download', 'account_balance')]
    return result
