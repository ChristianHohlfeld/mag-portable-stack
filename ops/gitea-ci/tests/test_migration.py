"""Native bridge against isolated HTTP GitHub/Gitea fixtures; no real credentials."""
# mag-test-timeout: 180
import pathlib as _mag_path
import sys as _mag_sys
_mag_root = _mag_path.Path(__file__).resolve()
while _mag_root != _mag_root.parent and not (_mag_root / 'ops/ci/mag_deadline.py').is_file():
    _mag_root = _mag_root.parent
_mag_sys.path.insert(0, str(_mag_root / 'ops/ci'))
import mag_deadline as _mag_deadline
_mag_deadline.arm(180)
import hashlib,hmac,http.client,http.server,json,os,pathlib,socket,subprocess,tempfile,threading,time,unittest
ROOT=pathlib.Path(__file__).resolve().parents[3]
BINARY=ROOT/'build/mag-ci-bridge'
GUARD=ROOT/'build/mag-ci-guard'
CONTEXTS=json.loads((ROOT/'ops/gitea-ci/policy.json').read_text())['contexts']
A,B,C,D=('a'*40,'b'*40,'c'*40,'d'*40)
class Backend(http.server.BaseHTTPRequestHandler):
 def log_message(self,*a): pass
 def answer(self,code,obj=None):
  data=json.dumps(obj if obj is not None else {}).encode();self.send_response(code);self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
 def do_GET(self):self.handle_api()
 def do_POST(self):self.handle_api()
 def do_PATCH(self):self.handle_api()
 def handle_api(self):
  f=self.server.fixture;p=self.path.split('?',1)[0];n=int(self.headers.get('Content-Length',0));body=json.loads(self.rfile.read(n) or b'{}')
  f.requests.append((self.command,p,body))
  expected='token fixture-gh' if p.startswith('/github/') else 'token fixture-gt'
  if self.headers.get('Authorization')!=expected:return self.answer(401)
  gp='/github/repos/ChristianHohlfeld/mag-portable-stack';tp='/gitea/repos/mag-ci/mag-portable-stack'
  if p==gp+'/branches/main':return self.answer(200,{'commit':{'sha':A}})
  if p==gp+'/branches':return self.answer(200,[{'name':'main','commit':{'sha':A}}])
  if p==gp+'/pulls':return self.answer(200,[{'number':128,'head':{'sha':B,'repo':{'clone_url':'https://attacker.invalid/evil.git'}},'base':{'sha':A}}] if f.pr else [])
  if p==gp+'/git/ref/heads/main':return self.answer(200,{'object':{'sha':D if f.moved else A}})
  if p==gp+'/git/ref/heads/green':return self.answer(404)
  if self.command=='POST' and '/statuses/' in p:f.statuses.append((p.rsplit('/',1)[1],body));return self.answer(201,body)
  if self.command in ('POST','PATCH') and p.startswith(gp+'/git/refs'):f.promotions.append(body);return self.answer(201,body)
  if p==tp+'/branches/mag-ci%2Fcontrol':return self.answer(200,{'commit':{'id':C}})
  if p==tp+'/actions/workflows/mag-ci.yml/dispatches':
   f.dispatches.append(body);f.run_sha[len(f.dispatches)]=body['inputs']['sha'];return self.answer(204 if f.lost else 200,{'workflow_run_id':len(f.dispatches)})
  if p==tp+'/actions/workflows/mag-ci.yml/runs':return self.answer(200,{'workflow_runs':[{'id':i,'head_sha':C,'display_title':'MAG CI '+sha,'event':'workflow_dispatch'} for i,sha in f.run_sha.items()]})
  if p.startswith(tp+'/actions/runs/') and p.endswith('/jobs'):
   i=int(p.split('/')[-2]);steps=[{'name':c,'status':'completed','conclusion':'success'} for c in CONTEXTS]
   if f.missing:steps=[s for s in steps if s['name']!=f.missing]
   if f.skipped:next(s for s in steps if s['name']==f.skipped)['conclusion']='skipped'
   if f.duplicate:steps.append(steps[0])
   if f.pending:steps[-1]['status']='queued';steps[-1]['conclusion']=''
   return self.answer(200,{'jobs':[{'id':i*10,'run_id':i,'head_sha':D if f.wrong_sha else C,'name':'checks','labels':['mag-ci-vm' if not f.wrong_label else 'mag'],'status':'queued' if f.pending else 'completed','conclusion':'' if f.pending else 'success','steps':steps}]})
  self.answer(404)
class NativeBridge(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  if not BINARY.exists() or not GUARD.exists():raise unittest.SkipTest('native CI targets not built')
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.path=pathlib.Path(self.tmp.name);(self.path/'control').mkdir();(self.path/'creds').mkdir()
  for name,val in [('github','fixture-gh'),('gitea','fixture-gt'),('webhook','fixture-hook')]:
   p=self.path/'creds'/name;p.write_text(val);p.chmod(0o600)
  self.requests=[];self.statuses=[];self.promotions=[];self.dispatches=[];self.run_sha={}
  self.pr=self.moved=self.lost=self.pending=self.wrong_sha=self.wrong_label=self.duplicate=False;self.missing=self.skipped=''
  self.server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Backend);self.server.fixture=self
  self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
  self.policy=json.loads((ROOT/'ops/gitea-ci/policy.json').read_text());base='http://127.0.0.1:'+str(self.server.server_port)
  self.policy.update(github_api=base+'/github',gitea_api=base+'/gitea');self.config=self.path/'policy.json';self.config.write_text(json.dumps(self.policy))
  self.env=dict(os.environ,MAG_CI_TEST='1',MAG_CI_ONCE='1',CREDENTIALS_DIRECTORY=str(self.path/'creds'))
 def tearDown(self):self.server.shutdown();self.server.server_close();self.tmp.cleanup()
 def run_bridge(self,ok=True):
  r=subprocess.run([str(BINARY),str(self.config),str(self.path/'control')],env=self.env,capture_output=True,text=True,timeout=15)
  if ok:self.assertEqual(r.returncode,0,r.stderr)
  else:self.assertNotEqual(r.returncode,0)
  self.assertNotIn('fixture-gh',r.stdout+r.stderr);self.assertNotIn('fixture-gt',r.stdout+r.stderr);return r
 def test_success_exact_contexts_and_main_promotion(self):
  self.run_bridge();success=[b['context'] for sha,b in self.statuses if sha==A and b['state']=='success']
  self.assertCountEqual(success,CONTEXTS);self.assertEqual(self.promotions,[{'sha':A,'force':False,'ref':'refs/heads/green'}])
 def test_fork_pr_uses_target_repo_ref_and_never_promotes_pr(self):
  self.pr=True;self.run_bridge();state=json.loads((self.path/'control/state.json').read_text())
  self.assertEqual(state[B]['ref'],'refs/pull/128/head');self.assertTrue(any(sha==B and b['state']=='success' for sha,b in self.statuses));self.assertTrue(all(p['sha']==A for p in self.promotions))
  self.assertFalse(any('attacker' in p for _,p,_ in self.requests))
 def test_missing_required_step_is_failure(self):
  self.missing='speech';self.run_bridge();self.assertTrue(any(b['context']=='speech' and b['state']=='failure' for _,b in self.statuses));self.assertFalse(self.promotions)
 def test_skipped_is_not_success(self):
  self.skipped='e2e-bootstrap';self.run_bridge();self.assertFalse(self.promotions)
 def test_duplicate_step_is_not_success(self):
  self.duplicate=True;self.run_bridge();self.assertFalse(self.promotions)
 def test_wrong_control_sha_is_rejected(self):
  self.wrong_sha=True;self.run_bridge(False);self.assertFalse(self.promotions);self.assertTrue(all(b['state']=='pending' for _,b in self.statuses))
 def test_wrong_worker_is_rejected(self):
  self.wrong_label=True;self.run_bridge(False);self.assertFalse(self.promotions)
 def test_old_main_cannot_promote(self):
  self.moved=True;self.run_bridge();self.assertFalse(self.promotions)
 def test_queued_requests_one_disposable_worker(self):
  self.pending=True;self.run_bridge();self.assertEqual((self.path/'request').read_text(),'1\n');self.assertFalse(self.promotions)
 def test_restart_does_not_duplicate_dispatch_or_statuses(self):
  self.run_bridge();n=len(self.statuses);self.run_bridge();self.assertEqual(len(self.dispatches),1);self.assertEqual(len(self.statuses),n)
 def test_lost_dispatch_response_is_reconciled_by_identity(self):
  self.lost=True;self.run_bridge(False);self.lost=False;self.run_bridge();self.assertEqual(len(self.dispatches),1);self.assertTrue(self.promotions)
 def test_other_repository_policy_rejected_without_requests(self):
  self.policy['github_repository']='other/repo';self.config.write_text(json.dumps(self.policy));self.run_bridge(False);self.assertFalse(self.requests)
 def test_signed_webhook_and_malformed_length(self):
  env=dict(self.env);env.pop('MAG_CI_ONCE');proc=subprocess.Popen([str(BINARY),str(self.config),str(self.path/'control')],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
  try:
   for _ in range(100):
    try:s=socket.create_connection(('127.0.0.1',3011),.2);s.close();break
    except OSError:time.sleep(.02)
   def post(repo,valid=True):
    body=json.dumps({'repository':{'full_name':repo}}).encode();sig='sha256='+hmac.new(b'fixture-hook',body,hashlib.sha256).hexdigest()
    c=http.client.HTTPConnection('127.0.0.1',3011,timeout=4);c.request('POST','/github',body,{'X-Hub-Signature-256':sig if valid else 'sha256='+'0'*64});r=c.getresponse();code=r.status;r.read();c.close();return code
   self.assertEqual(post('ChristianHohlfeld/mag-portable-stack'),202);self.assertEqual(post('other/repo'),403);self.assertEqual(post('ChristianHohlfeld/mag-portable-stack',False),403)
   s=socket.create_connection(('127.0.0.1',3011),1);s.sendall(b'POST /github HTTP/1.1\r\nContent-Length: 999999999999999999999\r\n\r\n');self.assertIn(b'403',s.recv(200));s.close();self.assertIsNone(proc.poll())
  finally:proc.terminate();proc.wait(timeout=6)
 def test_guard_unknown_and_busy_fail_closed(self):
  for value,idle in [([],False),([{}],False),([{'is_processing':True}],False),([{'state':1}],False),([{'state':0}],True),([{'is_processing':False}],True),([{'is_processing':False},{'state':1}],False)]:
   p=self.path/'slots.json';p.write_text(json.dumps(value));r=subprocess.run([str(GUARD),'--test',str(p)],capture_output=True);self.assertEqual(r.returncode==0,idle,value)
class WorkflowPolicy(unittest.TestCase):
 def test_github_triggers_are_path_filtered(self):
  import yaml
  def triggers(doc):
   return doc.get('on', doc.get(True)) or {}
  dispatch_only={'publish-mag-stack-4.0.4.yml','green.yml'}
  for p in sorted((ROOT/'.github/workflows').glob('*.yml')):
   on=triggers(yaml.safe_load(p.read_text()))
   if p.name in dispatch_only:
    self.assertEqual(set(on),{'workflow_dispatch'},p.name)
    continue
   self.assertEqual(set(on),{'workflow_dispatch','push','pull_request'},p.name)
   self.assertEqual(on['push'].get('branches'),['main'],p.name)
   self.assertNotIn('paths', on['push'] or {}, p.name)
   self.assertTrue(on['pull_request'].get('paths'), p.name)
  self.assertNotIn('workflow_run', (ROOT/'.github/workflows/green.yml').read_text())
 def test_all_legacy_run_steps_are_accounted_for(self):
  import yaml
  coverage=json.loads((ROOT/'ops/gitea-ci/coverage.json').read_text())
  names={'native':'native.yml','pages':'pages-native.yml','nginx':'private-nginx.yml','recovery':'backup.yml','room':'room-e2e.yml','chromium':'image-paste.yml','speech':'speech.yml','e2e-bootstrap':'e2e-bootstrap.yml'}
  for suite,file in names.items():
   doc=yaml.safe_load((ROOT/'.github/workflows'/file).read_text())
   actual=[]
   for job in doc['jobs'].values():
    for s in job.get('steps') or []:
     if 'run' in s: actual.append(s.get('name') or 'run')
   self.assertEqual(sorted(actual), sorted(r['name'] for r in coverage[suite]), file)
   for r in coverage[suite]:self.assertIn(r['kind'],('test','image-dependency'))
 def test_control_workflow_runs_every_required_suite_even_after_failure(self):
  import yaml
  doc=yaml.safe_load((ROOT/'.gitea/workflows/mag-ci.yml').read_text());job=doc['jobs']['checks'];steps=job['steps']
  self.assertEqual(job['runs-on'],'mag-ci-vm');self.assertEqual([s['name'] for s in steps[1:]],CONTEXTS)
  self.assertTrue(all(s['if']=='always()' for s in steps[1:]));self.assertEqual(doc['permissions'],{'contents':'read'})
if __name__=='__main__':unittest.main(verbosity=2)
