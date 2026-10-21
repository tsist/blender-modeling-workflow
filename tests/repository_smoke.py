"""Offline repository/job/package checks. No credentials, Blender or network."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from unittest.mock import patch
import urllib.parse
import zipfile

sys.dont_write_bytecode=True
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'tools/blenderctl'),str(ROOT/'tools'),str(ROOT/'scripts')]
import protocol
import runner
from modeling_identity import implementation_sha256
from modeling_workflow_addon.providers import tripo_overseas
from build_release import ADDON, CLI, SKILL, SUPPORT, build

def main():
    checks=[]
    def check(name,condition):
        assert condition,name
        checks.append(name)
    def invoke(*args):
        return subprocess.run([sys.executable,'-B',str(ROOT/'scripts/modeling_workflow.py'),*args],
                              capture_output=True,text=True,encoding='utf8',timeout=20)
    version=invoke('--version');check('portable entry version',version.returncode==0 and version.stdout.strip()=='0.1.13')
    process=invoke('modeling','describe');description=json.loads(process.stdout)
    check('standalone discovery',process.returncode==0 and description['implementation_sha256']==implementation_sha256())
    check('all providers explicit',set(row['provider'] for row in description['provider_catalog']['providers'])=={'tripo-cn','tripo-overseas','meshy'})
    for provider,operation in [('tripo-cn','h31_image'),('tripo-overseas','account_balance'),('tripo-overseas','h31_multiview'),('meshy','image_to_3d')]:
        result=invoke('modeling','describe','--provider',provider,'--provider-operation',operation,'--schema')
        check(provider+' '+operation+' schema',result.returncode==0 and 'schema' in json.loads(result.stdout))
    unsupported=invoke('modeling','describe','--provider','tripo-overseas','--provider-operation','resume_download')
    check('unsupported operation fails',unsupported.returncode!=0 and json.loads(unsupported.stdout)['ok'] is False)
    check('no generic tool dispatch',invoke('material','run').returncode==2)

    manifest={'schema_version':'1.1','provider':'tripo-overseas','operation':'account_balance',
              'credential_ref':{'provider':'tripo-overseas','region':'global',
                                'reference':{'kind':'environment','name':'UNREAD_REPOSITORY_FAKE_KEY'}}}
    request={'schema_version':'1.0','command':'modeling.run','params':{'manifest':manifest}}
    calls=[]
    class Fake:
        def api(self,route,reference,payload=None):
            assert route=='/account/balance' and payload is None
            calls.append(route);return {'balance':1.25,'frozen':0.01}
    with tempfile.TemporaryDirectory(prefix='modeling-repository-smoke-') as temporary:
        temp=Path(temporary).resolve()
        if os.name=='nt':
            with patch.object(tripo_overseas,'OverseasHTTPTransport',Fake):
                job=runner.prepare(request,root=temp/'jobs')
                result,code=runner.execute(job)
                check('real host lifecycle with fake GET',code==0 and result['ok'] and calls==['/account/balance'])
                check('successful terminal durable',protocol.read_json(job/'status.json')['state']=='succeeded')
                try:runner.execute(job)
                except protocol.Failure as error:check('finished job not replayed',error.code=='CONFLICT' and len(calls)==1)
                else:raise AssertionError('completed job replay accepted')
                queued=runner.prepare(request,root=temp/'jobs')
                changed=protocol.read_json(queued/'request.json');changed['params']['implementation_sha256']='0'*64
                protocol.atomic_json(queued/'request.json',changed)
                result,code=runner.execute(queued)
                check('queued request drift durable failure',code==4 and (queued/'result.json').is_file() and protocol.read_json(queued/'status.json')['state']=='failed' and len(calls)==1)
                cancelled=runner.prepare(request,root=temp/'jobs');runner.cancel(cancelled)
                result,code=runner.execute(cancelled)
                check('queued cancellation no remote action',code==7 and protocol.read_json(cancelled/'status.json')['state']=='cancelled' and len(calls)==1)
        else:
            try:runner.prepare(request,root=temp/'jobs')
            except protocol.Failure as error:check('non-Windows execution explicitly refused',error.code=='UNSUPPORTED')
            else:raise AssertionError('unsupported OS accepted')
        duplicate=temp/'duplicate.json';duplicate.write_text('{"schema_version":"1.0","schema_version":"1.0"}',encoding='utf8')
        check('duplicate JSON rejected',invoke('request',str(duplicate)).returncode==2)
        install=[sys.executable,'-B',str(ROOT/'scripts/install_skill.py'),'--destination',str(temp/'skill-root')]
        first=subprocess.run(install,capture_output=True,text=True,timeout=20)
        installed=temp/'skill-root/blender-modeling-workflow/SKILL.md'
        check('one skill installer',first.returncode==0 and installed.read_bytes()==(ROOT/'skills/blender-modeling-workflow/SKILL.md').read_bytes())
        check('existing skill protected',subprocess.run(install,capture_output=True,timeout=20).returncode!=0)
        left=build(temp/'release-a');right=build(temp/'release-b')
        check('all release archives reproducible',all((temp/'release-a'/p['file']).read_bytes()==(temp/'release-b'/p['file']).read_bytes() for p in left['packages']))
        for package in left['packages']:
            with zipfile.ZipFile(temp/'release-a'/package['file']) as archive:
                check(package['file']+' CRC',archive.testzip() is None)
                check(package['file']+' manifest',set(archive.namelist())=={row['file'] for row in package['members']})
        source=temp/'release-a'/left['packages'][2]['file']
        with zipfile.ZipFile(source) as archive:archive.extractall(temp/'extracted')
        extracted=temp/'extracted/blender-modeling-workflow'
        consumer=subprocess.run([sys.executable,'-I','-B',str(extracted/'scripts/modeling_workflow.py'),'modeling','describe'],
                                capture_output=True,text=True,encoding='utf8',timeout=20,cwd=temp)
        check('extracted source discovery independent',consumer.returncode==0 and json.loads(consumer.stdout)['implementation_sha256']==implementation_sha256())
        expected={*(f'tools/modeling_workflow_addon/{p}' for p in ADDON),*(f'skills/blender-modeling-workflow/{p}' for p in SKILL),*(f'tools/blenderctl/{p}' for p in CLI),*SUPPORT}
        check('source whitelist exact',set(p.relative_to(extracted).as_posix() for p in extracted.rglob('*') if p.is_file())==expected)

    files=[ROOT/name for name in SUPPORT if name.endswith('.md')]
    files.extend((ROOT/'skills/blender-modeling-workflow').rglob('*.md'))
    for path in files:
        text=path.read_text('utf8')
        for link in re.findall(r'\[[^\]]*\]\(([^)]+)\)',text):
            target=link.strip('<>').split('#',1)[0]
            if target and not re.match(r'[a-zA-Z]+:',target):
                assert (path.parent/urllib.parse.unquote(target)).exists(),str(path)+': '+target
        assert not re.search(r'Q4[234]|runtime[/\\]audit|wm-2026|C:[/\\]Users|F:[/\\]Blender',text),str(path)
    check('portable docs and local links',True)
    text=(ROOT/'skills/blender-modeling-workflow/SKILL.md').read_text('utf8')
    check('single skill frontmatter',text.startswith('---\n') and 'name: blender-modeling-workflow' in text.split('---',2)[1] and 'description:' in text.split('---',2)[1])
    print(json.dumps({'status':'pass','checks':len(checks),'network_calls':0,'blender_calls':0,'real_credentials_read':0}))
    return 0

if __name__=='__main__':raise SystemExit(main())
