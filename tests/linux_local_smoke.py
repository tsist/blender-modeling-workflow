"""Offline Linux unit checks plus optional real Blender GLB import and fresh reopen."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import struct
import time
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'tools/blenderctl'), str(ROOT/'tools')]
from linux_local import create_job, regular, run_group
from protocol import Failure


def fixture(path):
    # One nondegenerate triangle with positive height, embedded resources only.
    data = struct.pack('<9f', 0,0,0, 1,0,0, 0,1,0)
    doc = {'asset':{'version':'2.0'},'buffers':[{'byteLength':len(data)}],
           'bufferViews':[{'buffer':0,'byteLength':len(data),'target':34962}],
           'accessors':[{'bufferView':0,'componentType':5126,'count':3,'type':'VEC3',
                         'min':[0,0,0],'max':[1,1,0]}],
           'meshes':[{'primitives':[{'attributes':{'POSITION':0}}]}],
           'nodes':[{'mesh':0}],'scenes':[{'nodes':[0]}],'scene':0}
    js = json.dumps(doc).encode()
    js += b' ' * (-len(js) % 4)
    content = struct.pack('<I4s',len(js),b'JSON')+js+struct.pack('<I4s',len(data),b'BIN\0')+data
    path.write_bytes(struct.pack('<4sII',b'glTF',2,len(content)+12)+content)


class LocalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.src = self.root/'sample.glb'
        fixture(self.src)
        self.sha = hashlib.sha256(self.src.read_bytes()).hexdigest()

    def invoke(self, operation='import_local', expected=None, args=(), blender=None, source=None):
        path = self.root/'params.json'
        manifest = {'schema_version':'1.1','provider':'local','operation':operation,
                    'input':{'file':str(source or self.src),'expected_sha256':expected or self.sha},
                    'preview':{'enabled':False}}
        path.write_text(json.dumps({'manifest':manifest}))
        proc = subprocess.run([sys.executable,'-B',str(ROOT/'scripts/modeling_workflow.py'),
            '--blender',str(blender or sys.executable),'--jobs-dir',str(self.root/'jobs'),
            *args,'modeling','run','--params',str(path)],capture_output=True,text=True,timeout=90)
        result=json.loads(proc.stdout)
        self.assertEqual(proc.returncode==0,result['ok'])
        return result

    def test_unsupported_before_job(self):
        for case in (dict(operation='submit'),dict(args=['--async'])):
            self.assertEqual(self.invoke(**case)['error']['code'],'UNSUPPORTED')
        self.assertFalse((self.root/'jobs').exists())

    def test_bad_hash(self):
        result=self.invoke(expected='0'*64)
        self.assertEqual(result['error']['code'],'CONFLICT')
        job=Path(result['artifacts']['job_directory'])
        self.assertEqual(json.loads((job/'status.json').read_text())['state'],'failed')
        self.assertFalse((job/'modeling-report.json').exists())

    def test_path_types(self):
        alias=self.root/'alias.glb';alias.symlink_to(self.src)
        self.assertFalse(self.invoke(source=alias)['ok'])
        hard=self.root/'hard.glb';os.link(self.src,hard)
        with self.assertRaises(Failure):
            with regular(hard):pass
        self.assertEqual(hashlib.sha256(self.src.read_bytes()).hexdigest(),self.sha)

    def test_private_new_jobs(self):
        jobs=self.root/'jobs';jobs.mkdir(mode=0o700)
        a,b=create_job(jobs),create_job(jobs)
        self.assertNotEqual(a,b);self.assertEqual(a.stat().st_mode & 0o777,0o700)
        jobs.chmod(0o755)
        with self.assertRaises(Failure):create_job(jobs)

    def test_deadline(self):
        result=self.invoke(args=['--timeout','0.000001'])
        self.assertEqual(result['error']['code'],'TIMEOUT')
        job=Path(result['artifacts']['job_directory'])
        self.assertFalse((job/'modeling-report.json').exists())

    def test_bounded_group_cleanup(self):
        # A trusted helper and ordinary child simulate the worker/reader pair.
        marker=self.root/'reader.pid'
        child="import time;time.sleep(20)"
        helper=("import pathlib,subprocess,sys,time;"
                "p=subprocess.Popen([sys.executable,'-c',sys.argv[2]]);"
                "pathlib.Path(sys.argv[1]).write_text(str(p.pid));time.sleep(20)")
        deadline=time.monotonic()+5
        def cancel():
            if time.monotonic()>deadline:raise AssertionError('fixture child never started')
            if marker.exists() and marker.stat().st_size:raise Failure('CANCELLED','fixture complete')
        with open(os.devnull,'wb') as log, self.assertRaises(Failure):
            run_group([sys.executable,'-I','-c',helper,str(marker),child],
                      self.root,{'PATH':os.defpath},log,log,cancel)
        pid=int(marker.read_text())
        for _ in range(100):
            statfile=Path(f'/proc/{pid}/stat')
            try:state=statfile.read_text().rsplit(')',1)[1].split()[0]
            except FileNotFoundError:break
            if state=='Z':break
            time.sleep(.01)
        else:self.fail('owned reader remained running after cancellation')

    def test_blender(self):
        if not BLENDER:self.skipTest('optional: --blender PATH')
        r=self.invoke(blender=BLENDER);self.assertTrue(r['ok'],r.get('error'))
        d=r['data'];self.assertEqual(d['fresh_process_reopen'],'pass')
        self.assertEqual(d['verification']['process'],'fresh_process')
        self.assertEqual(d['content']['meshes'][0]['triangles'],1)
        self.assertEqual(hashlib.sha256(self.src.read_bytes()).hexdigest(),self.sha)
        self.assertEqual(hashlib.sha256(Path(d['candidate']).read_bytes()).hexdigest(),d['candidate_sha256'])


BLENDER=None
if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--blender',type=Path)
    BLENDER=parser.parse_args().blender
    unittest.main(argv=[sys.argv[0]],verbosity=2)
