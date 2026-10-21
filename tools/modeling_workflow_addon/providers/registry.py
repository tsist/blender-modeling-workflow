"""Explicit adapter selection. Discovery never resolves credentials or calls APIs."""
import copy
from .common import ProviderError

CATALOG_VERSION = '1.0'
PROVIDERS = ('tripo-cn', 'meshy', 'tripo-overseas')
MESHY_ROUTES = {'image_to_3d': '/v1/image-to-3d/',
                'multi_image_to_3d': '/v1/multi-image-to-3d/',
                'text_to_3d': '/v2/text-to-3d/',
                'retexture': '/v1/retexture/', 'remesh': '/v1/remesh/'}


def require_provider(provider):
    if provider not in PROVIDERS:
        raise ProviderError('Provider is not registered; no implicit fallback')
    return provider


def catalog(provider=None, provider_operation=None):
    if provider is None:
        if provider_operation is not None:
            raise ProviderError('Provider operation requires an explicit provider')
        values = [catalog(p) for p in PROVIDERS]
        return {'catalog_version': CATALOG_VERSION, 'default_legacy_provider': 'tripo-cn',
                'providers': [{'provider':v['provider'], 'adapter_version':v['adapter_version'],
                    'operations':[{'operation':r['operation'],'adapter_state':r['adapter_state'],'evidence':r['evidence']} for r in v['operations']]} for v in values],
                'discovery':'modeling describe --provider <id> --provider-operation <operation> [--schema]',
                'selection': 'Explicit selection only; task IDs and budgets never migrate between providers'}
    require_provider(provider)
    if provider == 'tripo-cn':
        from .tripo_cn import capabilities
        from ..contract import schemas, H31_PARTS_OPERATION, h31_parts_request_schema
        value = capabilities()
        schema = schemas()['submit']['properties']['request']
        rows = []
        for row in value['operations']:
            row = dict(row)
            if row['operation'] == H31_PARTS_OPERATION:
                row['adapter_state'] = 'executable'
                row['evidence'] = {'official_docs_verified_on': '2026-10-14',
                                   'documentation': True, 'offline_adapter_tests': True,
                                   'historical_service_runtime': True, 'public_paid_submit': True,
                                   'runtime_not_run': False, 'case': 'H2',
                                   'verified_on': '2026-10-14', 'task_type': 'multiview_to_model',
                                   'observed_credits': 40, 'observed_mesh_objects': 12,
                                   'semantic_quality': 'partial_fail', 'texture_preservation': 'not_run',
                                   'one_case_only': True}
                row['request_schema'] = h31_parts_request_schema()
                row['input_views'] = ['front', 'back']
                row['manifest_scope'] = '1.1 only; explicit provider=tripo-cn'
                row['output_contract'] = 'Actual supported GLB model_url only; H2 observed 12 mesh objects, not a universal name or hierarchy guarantee. Semantic body/clothing layers and duplicate pouches partial_fail; texture preservation not_run; one case only.'
                rows.append(row)
                continue
            row['adapter_state'] = 'executable'
            row['evidence'] = {'historical_service_runtime': True,
                               'public_paid_submit': row['operation'] in ('h31_image', 'h31_multiview'),
                               'offline_adapter_tests': True}
            row['request_schema'] = copy.deepcopy(schema)
            row['request_schema']['properties']['operation'] = {'const': row['operation']}
            if row['operation'] in ('h31_image', 'h31_multiview', 'p2_multiview'):
                count = 1 if row['operation'] == 'h31_image' else 2
                row['request_schema']['required'] += ['inputs']
                row['request_schema']['properties'].pop('source_task_id')
                row['request_schema']['properties']['inputs'].update(minItems=count, maxItems=count)
                item = row['request_schema']['properties']['inputs']['items']
                row['request_schema']['properties']['inputs']['prefixItems'] = []
                for view in (['front'] if count == 1 else ['front','back']):
                    ordered = copy.deepcopy(item)
                    ordered['properties']['view'] = {'const':view}
                    row['request_schema']['properties']['inputs']['prefixItems'].append(ordered)
                row['input_views'] = ['front'] if count == 1 else ['front', 'back']
            else:
                row['request_schema']['required'] += ['source_task_id']
                row['request_schema']['properties'].pop('inputs')
            row['output_contract'] = 'Known-task GLB only; importer rejects unsupported GLB features'
            rows.append(row)
        value.update(catalog_version=CATALOG_VERSION, operations=rows,
                     adapter_version='tripo-cn-fixed-v1',
                     documentation_only=['text-to-model', 'other model versions', 'image autofix',
                         'model_seed', 'other generate_parts inputs/options', 'smart_low_poly', 'quad/FBX', 'conversion'],
                     finance={'unit':'Tripo CN credits', 'ledger':'Tripo-only approval-bound ledger; no cross-provider reuse'})
    elif provider == 'tripo-overseas':
        from .overseas_contract import BASE, REGION, MODEL, OPERATIONS, WIRE_OPTIONS, schemas
        definitions = schemas()
        rows = []
        for index, operation in enumerate(OPERATIONS):
            request = copy.deepcopy(definitions['overseas_request']['oneOf'][index])
            manifest = copy.deepcopy(definitions['overseas_submit'])
            manifest['properties']['request'] = request
            rows.append({'operation': operation, 'adapter_state': 'executable_basic',
                         'request_schema': request, 'manifest_schema': manifest,
                         'evidence': {'official_docs_verified_on': '2026-10-21',
                                      'offline_adapter_tests': True, 'live_generation': False}})
        for action in ('query', 'download', 'account_balance'):
            rows.append({'operation': action, 'adapter_state': 'executable_basic',
                         'manifest_schema': copy.deepcopy(definitions['overseas_' + action]),
                         'evidence': {'official_docs_verified_on': '2026-10-21',
                                      'offline_adapter_tests': True, 'live_generation': False}})
        value = {'catalog_version': CATALOG_VERSION, 'provider': provider,
                 'region': REGION, 'base_url': BASE, 'model': MODEL, 'fixed_wire': dict(WIRE_OPTIONS),
                 'adapter_version': 'tripo-overseas-basic-v1', 'operations': rows,
                 'actions': ['submit', 'query', 'download', 'account_balance'],
                 'finance': {'unit': 'Tripo overseas credits', 'ledger': 'Independent approval-bound overseas ledger; no CN or research-ledger reuse',
                             'published_base_estimate': 20, 'server_cost_cap_guaranteed': False},
                 'unsupported': ['batch', 'resume_download', 'bind_unknown', 'ledger migration', 'cross-region fallback'],
                 'limits': ['Fixed H3.1 standard untextured PNG front or front/back generation only',
                            'Basic mocked adapter checks; no live model generation or Blender acceptance',
                            'Unknown submission/upload retains reservation; no automatic retries',
                            'Missing terminal credits_consumed retains reservation; no assumed zero or fixed cost',
                            'Download is a fresh bounded GLB transfer; CN checkpoints are not accepted']}
    else:
        from .meshy import task_schema
        value = {'catalog_version': CATALOG_VERSION, 'provider': 'meshy',
                 'base_url': 'https://api.meshy.ai/openapi', 'region': 'account/service dependent; no CN equivalence claim',
                 'adapter_version': 'meshy-known-task-query-v1',
                 'operations': [{'operation': op, 'query_route': route + '{task_id}',
                     'adapter_state': 'read_only_query',
                     'evidence': {'official_docs_verified_on':'2026-10-13', 'offline_adapter_tests':True,
                                  'live_service_runtime':False},
                     'manifest_schema': task_schema(op),
                     'generation_submission':'not_implemented',
                     'output_contract':'Available format names only; signed URLs never persisted'}
                    for op, route in MESHY_ROUTES.items()],
                 'actions':['query'],
                 'documentation_only':['image generation', 'multi-image generation', 'text preview/refine',
                    'retexture', 'remesh', 'smart-topology/T2', 'geometry_resolution', 'download/recovery',
                    'paid submission', 'task deletion/refund'],
                 'unsupported':['submit', 'submit_batch', 'download', 'download_batch', 'resume_download',
                                'query_batch', 'bind_unknown', 'Tripo ledger or parent-task reuse'],
                 'finance':{'unit':'Meshy API credits', 'ledger':'not implemented; Tripo budget is not transferable'},
                 'limits':['Read-only adapter is offline-tested; real account/API/quality not verified',
                           'No automatic provider switch, upload, generation, deletion, or retry',
                           'API outputs generally expire after 3 days outside Enterprise; confirm account terms',
                           'Latest models and availability differ by endpoint/account; no quality parity claim'],
                 'sources':['https://docs.meshy.ai/en/api/image-to-3d',
                            'https://docs.meshy.ai/en/api/task-lifecycle',
                            'https://docs.meshy.ai/en/api/asset-retention']}
    if provider_operation is not None:
        found = [row for row in value['operations'] if row['operation'] == provider_operation]
        if not found:
            raise ProviderError('Operation is not registered for the selected provider; no fallback')
        return {'catalog_version': CATALOG_VERSION, 'provider':provider, 'operation':found[0]}
    from .documented import features
    value['documented_features'] = features(provider)
    return value


def run_manifest(manifest, job_directory, transport=None, checkpoint=None):
    from ..contract import normalize_manifest
    manifest = normalize_manifest(manifest)
    provider = manifest.get('provider', 'tripo-cn')
    require_provider(provider)
    if provider == 'tripo-cn':
        if manifest['operation'] in ('outstanding_increment_prepare', 'outstanding_increment_apply'):
            from .outstanding_increment import prepare, apply
            from .common import atomic_json, digest
            from pathlib import Path
            checkpoint = checkpoint or (lambda: None)
            checkpoint()
            prepared = prepare(manifest['source_descriptor'], manifest['request_descriptor'])
            checkpoint()
            if manifest['operation'] == 'outstanding_increment_prepare':
                plan_file = Path(job_directory).resolve() / 'outstanding-increment-plan.json'
                result = prepared.document()
                atomic_json(plan_file, result)
                result['plan_descriptor'] = {'file': str(plan_file), 'expected_sha256': digest(plan_file)}
            else:
                result = apply(prepared, manifest['authorization_descriptor'], str(Path(job_directory).resolve()),
                               cancel=checkpoint)
            result['operation'] = manifest['operation']
            result['ledger_source_descriptor'] = copy.deepcopy(manifest['source_descriptor'])
            return result
        if manifest['operation'] in ('approval_append_prepare', 'approval_append_apply'):
            from .approval_append import prepare, apply
            from .budget import TrustedAuthorization
            from .common import atomic_json, digest
            from pathlib import Path
            checkpoint = checkpoint or (lambda: None)
            checkpoint()
            prepared = prepare(manifest['source_descriptor'], manifest['request_descriptor'])
            checkpoint()
            if manifest['operation'] == 'approval_append_prepare':
                plan_file = Path(job_directory).resolve() / 'approval-append-plan.json'
                result = prepared.document()
                atomic_json(plan_file, result)
                result['plan_descriptor'] = {'file': str(plan_file), 'expected_sha256': digest(plan_file)}
            else:
                authorization = TrustedAuthorization.from_descriptor(manifest['authorization_descriptor'])
                checkpoint()
                result = apply(prepared, authorization, manifest['authorization_descriptor'],
                               str(Path(job_directory).resolve()))
            result['operation'] = manifest['operation']
            result['ledger_source_descriptor'] = copy.deepcopy(manifest['source_descriptor'])
            return result
        from .tripo_cn import run_manifest as run
        from ..contract import H31_PARTS_OPERATION
        requests = manifest.get('requests', []) if manifest['operation'] == 'submit_batch' else [manifest.get('request')]
        has_parts = any(isinstance(request, dict) and request.get('operation') == H31_PARTS_OPERATION
                        for request in requests)
        delegated = manifest
        if not has_parts and manifest['operation'] != 'resume_pre_submit':
            delegated = {k:v for k,v in manifest.items() if k != 'provider'}
            delegated['schema_version'] = '1.0'
        options = {}
        if transport is not None:
            options['transport'] = transport
        if checkpoint is not None:
            options['checkpoint'] = checkpoint
        return run(delegated, job_directory, **options)
    if provider == 'tripo-overseas':
        from .tripo_overseas import run_manifest as run
        return run(manifest, job_directory, transport=transport, checkpoint=checkpoint or (lambda: None))
    from .meshy import query
    checkpoint = checkpoint or (lambda: None)
    checkpoint()
    result = query(manifest, transport)
    checkpoint()
    return result
