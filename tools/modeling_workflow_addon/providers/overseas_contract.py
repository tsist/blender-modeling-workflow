"""Explicit overseas Tripo contracts; legacy China contracts remain unchanged."""
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re

from ..contract import (ContractError, canonical_path, credential_reference,
                        file_descriptor, identity, integer, obj)

PROVIDER = 'tripo-overseas'
REGION = 'global'
BASE = 'https://openapi.tripo3d.ai/v3'
MODEL = 'v3.1-20260211'
OPERATIONS = ('h31_image', 'h31_multiview')
WIRE_OPTIONS = {'geometry_quality': 'standard', 'texture': False, 'pbr': False,
                'generate_parts': False, 'quad': False, 'smart_low_poly': False}
HASH_DOMAIN = 'modeling-provider-request/tripo-overseas/1.0'
MAX_CREDITS = 1000000000
MAX_DOWNLOAD_BYTES = 2 * 1024 ** 3


def money_cents(value):
    """Convert finite nonnegative numeric credits to exact integer hundredths."""
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise ContractError('Expected bounded credits with at most two decimal places')
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or amount < 0 or amount > MAX_CREDITS:
            raise ValueError
        cents = amount * 100
        if cents != cents.to_integral_value():
            raise ValueError
        return int(cents)
    except (InvalidOperation, ValueError, OverflowError):
        raise ContractError('Expected bounded credits with at most two decimal places') from None


def money(value):
    cents = money_cents(value)
    return cents // 100 if cents % 100 == 0 else cents / 100


def task_id(value):
    value = identity(value)
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', value):
        raise ContractError('Task identity must be a bounded path-safe identifier')
    return value


def scoped_credential(value):
    obj(value, ('provider', 'region', 'reference'), ('provider', 'region', 'reference'))
    if value['provider'] != PROVIDER or value['region'] != REGION:
        raise ContractError('Explicit overseas credential scope required')
    return {'provider': PROVIDER, 'region': REGION,
            'reference': credential_reference(value['reference'])}


def normalize_request(value):
    obj(value, ('operation_id', 'operation', 'inputs'), ('operation_id', 'operation', 'inputs'))
    op = value['operation']
    if not isinstance(op, str) or op not in OPERATIONS:
        raise ContractError('Unsupported overseas generation operation')
    views = ('front',) if op == 'h31_image' else ('front', 'back')
    if not isinstance(value['inputs'], list) or len(value['inputs']) != len(views):
        raise ContractError('Overseas generation requires ordered PNG references')
    rows = []
    for row, view in zip(value['inputs'], views):
        obj(row, ('view', 'file', 'expected_sha256'), ('view', 'file', 'expected_sha256'))
        if row['view'] != view:
            raise ContractError('Overseas input views must preserve front/back order')
        descriptor = file_descriptor({key: row[key] for key in ('file', 'expected_sha256')})
        if Path(descriptor['file']).suffix.lower() != '.png':
            raise ContractError('Overseas generation input must be a PNG')
        rows.append({'view': view, **descriptor})
    return {'operation_id': identity(value['operation_id']), 'operation': op, 'inputs': rows}


def request_sha256(value):
    document = {'request_contract_version': HASH_DOMAIN, 'provider': PROVIDER,
                'region': REGION, 'base_url': BASE, 'model': MODEL,
                'wire_options': dict(WIRE_OPTIONS),
                'request': normalize_request(value)}
    return hashlib.sha256(json.dumps(document, sort_keys=True, separators=(',', ':'),
                                    ensure_ascii=False, allow_nan=False).encode('utf-8')).hexdigest()


def normalize_manifest(value):
    if not isinstance(value, dict) or value.get('schema_version') != '1.1' or value.get('provider') != PROVIDER:
        raise ContractError('Overseas adapter requires explicit manifest 1.1 and provider')
    action = value.get('operation')
    fields = ['schema_version', 'provider', 'operation', 'credential_ref']
    required = list(fields)
    if action == 'submit':
        fields += ['request', 'ledger_file', 'authorization_descriptor']
        required = list(fields)
    elif action in ('query', 'download'):
        fields += ['task_id', 'ledger_file']
        required += ['task_id']
        if action == 'download':
            fields += ['max_download_bytes']
            required += ['max_download_bytes']
    elif action != 'account_balance':
        raise ContractError('Unsupported overseas action; no provider fallback')
    obj(value, fields, required)
    out = {'schema_version': '1.1', 'provider': PROVIDER, 'operation': action,
           'credential_ref': scoped_credential(value['credential_ref'])}
    if 'ledger_file' in value:
        out['ledger_file'] = canonical_path(value['ledger_file'])
    if action == 'submit':
        out['request'] = normalize_request(value['request'])
        out['authorization_descriptor'] = file_descriptor(value['authorization_descriptor'])
        if canonical_path(out['authorization_descriptor']['file']) == out['ledger_file']:
            raise ContractError('Immutable authorization cannot alias mutable overseas ledger')
    if 'task_id' in value:
        out['task_id'] = task_id(value['task_id'])
    if action == 'download':
        out['max_download_bytes'] = integer(value['max_download_bytes'], 1, MAX_DOWNLOAD_BYTES)
    return out


def schemas():
    """Fresh JSON schemas for public discovery and manifest.oneOf composition."""
    def strict(properties, required=None):
        return {'type': 'object', 'additionalProperties': False,
                'properties': properties, 'required': list(properties) if required is None else required}
    ident = {'type': 'string', 'pattern': r'^[A-Za-z0-9_.-]{1,256}$',
             'not': {'anyOf': [{'enum': ['.', '..']}, {'pattern': r'^(Bearer|tsk_|tcli_|file_)'}]}}
    sha = {'type': 'string', 'pattern': '^[0-9a-f]{64}$'}
    absolute = {'type': 'string', 'minLength': 1, 'description': 'Absolute local path; runtime canonicalizes and rejects relative paths'}
    descriptor = strict({'file': absolute, 'expected_sha256': sha})
    credential = {'oneOf': [strict({'kind': {'const': 'environment'}, 'name': {'type': 'string', 'pattern': '^[A-Z][A-Z0-9_]{0,127}$'}}),
                            strict({'kind': {'enum': ['dpapi', 'dpapi_clixml']}, 'path': absolute})]}
    scoped = strict({'provider': {'const': PROVIDER}, 'region': {'const': REGION}, 'reference': credential})
    request_rows = []
    for op, views in (('h31_image', ('front',)), ('h31_multiview', ('front', 'back'))):
        rows = [strict({'view': {'const': view}, 'file': {**absolute, 'pattern': r'\.[pP][nN][gG]$'}, 'expected_sha256': sha}) for view in views]
        request_rows.append(strict({'operation_id': ident, 'operation': {'const': op},
                                   'inputs': {'type': 'array', 'minItems': len(rows), 'maxItems': len(rows), 'prefixItems': rows}}))
    request = {'oneOf': request_rows}
    positive_money = {'type': 'number', 'exclusiveMinimum': 0, 'maximum': MAX_CREDITS, 'multipleOf': 0.01}
    authorization = strict({'schema_version': {'const': '1.0'}, 'approved': {'const': True},
        'approval_id': ident, 'provider': {'const': PROVIDER}, 'region': {'const': REGION},
        'base_url': {'const': BASE}, 'ledger_file': absolute, 'total_cap_credits': positive_money,
        'max_attempts': {'type': 'integer', 'minimum': 1, 'maximum': 10000}, 'allows_upload': {'type': 'boolean'},
        'operations': {'type': 'array', 'minItems': 1, 'maxItems': 10000,
                       'items': strict({'operation_id': ident, 'operation': {'enum': list(OPERATIONS)},
                                        'request_sha256': sha, 'max_credits': positive_money})}})
    result = {'overseas_authorization': authorization, 'overseas_request': request,
              'overseas_credential': scoped}
    for action in ('submit', 'query', 'download', 'account_balance'):
        props = {'schema_version': {'const': '1.1'}, 'provider': {'const': PROVIDER},
                 'operation': {'const': action}, 'credential_ref': scoped}
        required = list(props)
        if action == 'submit':
            props.update(request=request, ledger_file=absolute, authorization_descriptor=descriptor)
            required = list(props)
        elif action in ('query', 'download'):
            props.update(task_id={'type': 'string', 'pattern': r'^[A-Za-z0-9_-]{1,128}$',
                                  'not': {'pattern': r'^(Bearer|tsk_|tcli_|file_)'}}, ledger_file=absolute)
            required += ['task_id']
            if action == 'download':
                props['max_download_bytes'] = {'type': 'integer', 'minimum': 1, 'maximum': MAX_DOWNLOAD_BYTES}
                required += ['max_download_bytes']
        result['overseas_' + action] = strict(props, required)
    return result
