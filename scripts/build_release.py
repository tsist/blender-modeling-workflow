"""Build deterministic plugin, single-skill and standalone source archives."""
import argparse
import hashlib
import json
from pathlib import Path
import tomllib
import zipfile

ROOT = Path(__file__).resolve().parents[1]
ADDON = ('LICENSE','README.md','blender_manifest.toml','__init__.py','contract.py','core.py','ui.py',
         'providers/__init__.py','providers/approval_append.py','providers/authorization_extension.py',
         'providers/batch.py','providers/budget.py','providers/common.py','providers/documented.py',
         'providers/download.py','providers/legacy_trial.py','providers/meshy.py','providers/query_batch.py',
         'providers/registry.py','providers/tripo_cn.py','providers/pre_submit_recovery.py',
         'providers/transport_diagnostics.py','providers/image_upload.py','providers/outstanding_increment.py',
         'providers/overseas_contract.py','providers/overseas_budget.py','providers/tripo_overseas.py')
SKILL = ('LICENSE','SKILL.md','references/blender-handoff.md','references/bounded-batches.md',
         'references/brief-and-reference.md','references/delivery-and-review.md',
         'references/generate-and-compare.md','references/material-cooperation.md',
         'references/parts-and-completion.md','references/provider-and-budget.md','references/recovery.md')
CLI = ('cli.py','protocol.py','runner.py','worker.py','modeling_identity.py','filesystem.py','processes.py',
       'bootstrap.py','timing.py','modeling_interface.py','modeling_workflow_contract.py',
       'modeling_host.py','modeling_workflow.py','modeling_verifier.py')
SUPPORT = ('.gitattributes','.gitignore','.github/workflows/basic.yml','LICENSE','README.md','CHANGELOG.md',
           'THIRD_PARTY_NOTICES.md','blenderctl.ps1','docs/CAPABILITIES.md','docs/SECURITY.md',
           'docs/INSTALL.md','docs/VERIFICATION.md','scripts/modeling_workflow.py',
           'scripts/install_skill.py','scripts/build_release.py','tests/provider_smoke.py',
           'tests/repository_smoke.py')

def sha(data):return hashlib.sha256(data).hexdigest()

def product_files(folder, names):
    found={p.relative_to(folder).as_posix() for p in folder.rglob('*')
           if p.is_file() and '__pycache__' not in p.parts and p.suffix not in ('.pyc','.pyo')}
    if found != set(names):raise ValueError('Product whitelist mismatch: '+str(folder.relative_to(ROOT)))
    return [folder/name for name in names]

def read_sources(paths):
    result={}
    for path in paths:
        if not path.resolve().is_relative_to(ROOT):raise ValueError('Source leaves repository')
        for ancestor in (path,*path.parents):
            if ancestor.is_symlink() or getattr(ancestor,'is_junction',lambda:False)():
                raise ValueError('Source may not traverse a link')
            if ancestor==ROOT:break
        result[path]=path.read_bytes()
    return result

def archive(path, sources, folder, prefix=''):
    rows=[]
    with zipfile.ZipFile(path,'x',compression=zipfile.ZIP_STORED) as output:
        for source in sorted(sources):
            name=prefix+source.relative_to(folder).as_posix();raw=sources[source]
            info=zipfile.ZipInfo(name,(1980,1,1,0,0,0));info.create_system=3
            info.external_attr=0o100644 << 16;info.compress_type=zipfile.ZIP_STORED
            output.writestr(info,raw)
            rows.append({'file':name,'bytes':len(raw),'sha256':sha(raw)})
    return {'file':path.name,'bytes':path.stat().st_size,'sha256':sha(path.read_bytes()),'members':rows}

def build(destination):
    destination=Path(destination).resolve()
    destination.mkdir(parents=True,exist_ok=True)
    addon=ROOT/'tools/modeling_workflow_addon';skill=ROOT/'skills/blender-modeling-workflow'
    addon_paths=product_files(addon,ADDON);skill_paths=product_files(skill,SKILL)
    cli_paths=product_files(ROOT/'tools/blenderctl',CLI)
    paths=addon_paths+skill_paths+cli_paths+[ROOT/name for name in SUPPORT]
    sources=read_sources(paths)
    version=tomllib.loads(sources[addon/'blender_manifest.toml'].decode('utf8'))['version']
    names=[f'modeling-workflow-addon-{version}.zip',f'blender-modeling-workflow-skill-{version}.zip',
           f'blender-modeling-workflow-{version}-source.zip']
    if any((destination/name).exists() for name in [*names,'release-manifest.json','SHA256SUMS.txt']):
        raise ValueError('Destination already contains a release; choose a fresh directory')
    packages=[archive(destination/names[0],{p:sources[p] for p in addon_paths},addon),
              archive(destination/names[1],{p:sources[p] for p in skill_paths},skill,'blender-modeling-workflow/'),
              archive(destination/names[2],sources,ROOT,'blender-modeling-workflow/')]
    if any(path.read_bytes()!=raw for path,raw in sources.items()):
        raise ValueError('Source changed during packaging; do not publish these artifacts')
    result={'version':version,'license':'GPL-3.0-or-later','packages':packages,
            'scope':'Windows modeling CLI, Blender add-on and one companion skill; no credentials, jobs, models or runtime binaries'}
    manifest=destination/'release-manifest.json';manifest.write_text(json.dumps(result,indent=2)+'\n',encoding='utf8')
    sums=[(p['file'],p['sha256']) for p in packages]+[(manifest.name,sha(manifest.read_bytes()))]
    (destination/'SHA256SUMS.txt').write_text(''.join(f'{checksum}  {name}\n' for name,checksum in sorted(sums)),encoding='utf8')
    return result

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--destination',type=Path,required=True)
    options=parser.parse_args();result=build(options.destination)
    print(json.dumps({'version':result['version'],'packages':[{'file':p['file'],'sha256':p['sha256']} for p in result['packages']]}))
