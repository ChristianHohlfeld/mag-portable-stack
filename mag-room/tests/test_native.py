"""Isolated native integration checks. Uses temporary state and a fake model."""
import hashlib,http.server,json,os,pathlib,socket,socketserver,subprocess,tempfile,threading,time,unittest,urllib.request,urllib.error,urllib.parse
ROOT=pathlib.Path(__file__).resolve().parents[2]
# Idle prompt prewarm adds background model calls; tests count Model.calls exactly.
os.environ.setdefault('MAG_ROOM_PREWARM','0')
NETMODE=pathlib.Path(tempfile.mkdtemp(prefix='magnet-'))/'netmode.sh';NETLOG=NETMODE.with_suffix('.log')
NETMODE.write_text('#!/bin/sh\necho "$1" >> %s\n'%NETLOG);NETMODE.chmod(0o755)
def port():
 with socket.socket() as s:s.bind(('127.0.0.1',0));return s.getsockname()[1]
GHPORT=port()
def _ui_token(ui):
 import re as _re
 html=urllib.request.urlopen(f'http://127.0.0.1:{ui}/',timeout=10).read().decode()
 return _re.search(r'name="mag-token" content="([^"]+)"',html).group(1)
def _rules(content):
 i=content.index('\n[Erinnerungen]');return content[:i]
def _mem(content):
 i=content.index('\n[Erinnerungen]');return content[i+1:]
class Model(http.server.BaseHTTPRequestHandler):
 protocol_version='HTTP/1.1';calls=[];delay=0;reply=None;script=[];health=200;last_prompt={};overflow_once=0
 def log_message(self,*a):pass
 def do_GET(self):
  code=200
  if self.path=='/health':
   code=type(self).health
   data={200:b'{"status":"ok"}',503:b'{"error":{"code":503,"message":"Loading model","type":"unavailable_error"}}'}.get(code,b'{"error":"upstream unavailable"}')
  else:data=b'{"data":[{"id":"deepseek-v4-flash-0731"}]}'
  self.send_response(code);self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
 def do_POST(self):
  obj=json.loads(self.rfile.read(int(self.headers['Content-Length'])));self.calls.append(obj)
  if type(self).overflow_once:
   type(self).overflow_once-=1
   error=b'{"error":{"code":400,"message":"request (35178 tokens) exceeds the available context size (32768 tokens)","type":"exceed_context_size_error"}}'
   self.send_response(400);self.send_header('Content-Length',str(len(error)));self.end_headers();self.wfile.write(error);return
  self.send_response(200);self.send_header('Transfer-Encoding','chunked');self.end_headers()
  if self.delay:time.sleep(self.delay)
  try:
   scripted=type(self).script.pop(0) if type(self).script else None
   chunks=scripted if scripted is not None else ([type(self).reply] if type(self).reply is not None else ['Hallo ','aus dem ','Modell.'])
   for piece in chunks:
    if isinstance(piece,dict) and '_sleep' in piece:
     time.sleep(piece['_sleep']);continue
    if isinstance(piece,dict) and '_chunk' in piece:event=json.dumps(piece['_chunk']).encode()
    else:
     delta=piece if isinstance(piece,dict) else {'content':piece}
     event=json.dumps({'choices':[{'delta':delta}]}).encode()
    for payload in [b'data: '+event+b'\n\n',b'']:
     if payload:self.wfile.write(('%x\r\n'%len(payload)).encode()+payload+b'\r\n');self.wfile.flush()
   parts=[]
   for m in obj.get('messages') or []:
    c=m.get('content');parts.append(c if isinstance(c,str) else json.dumps(c,ensure_ascii=False,sort_keys=True))
   text='\x1e'.join(parts);evaluated=max(1,len(text));cached=0;slot=obj.get('id_slot',None)
   def lcp(prev):
    n=0;lim=min(len(prev),len(text))
    while n<lim and prev[n]==text[n]:n+=1
    return n
   # Like ik_llama: a pinned id_slot reuses only that slot's last prompt; an
   # unpinned request goes through the --cache-ram store (several prompts).
   if obj.get('cache_prompt') is True and slot is not None:
    prev=type(self).last_prompt.get(slot)
    if isinstance(prev,str):cached=lcp(prev)
    type(self).last_prompt[slot]=text
   elif obj.get('cache_prompt') is True:
    ram=type(self).last_prompt.setdefault('ram',[])
    cached=max([lcp(p) for p in ram]+[0]);ram.append(text)
   stat=json.dumps({'choices':[],'tokens_cached':cached,'tokens_evaluated':evaluated,'timings':{'prompt_n':max(0,evaluated-cached),'prompt_per_second':40.0,'predicted_n':3,'predicted_per_second':8.0,'cache_n':cached}}).encode()
   payload=b'data: '+stat+b'\n\n';self.wfile.write(('%x\r\n'%len(payload)).encode()+payload+b'\r\n');self.wfile.flush()
   end=b'data: [DONE]\n\n';self.wfile.write(('%x\r\n'%len(end)).encode()+end+b'\r\n0\r\n\r\n');self.wfile.flush()
  except (BrokenPipeError,ConnectionResetError):pass
# Exercise actual native whois/HMAC instead of trusting test-forged identity headers.
WHOIS_PROFILES = {}
class WhoisFixture(socketserver.StreamRequestHandler):
 def handle(self):
  first=self.rfile.readline().decode('ascii','replace')
  while self.rfile.readline() not in (b'\r\n',b''):pass
  if not first.startswith('GET /localapi/v0/whois?addr='):return
  value=urllib.parse.parse_qs(urllib.parse.urlparse(first.split()[1]).query).get('addr',[''])[0]
  login,name=WHOIS_PROFILES.get(value,('',''))
  data=json.dumps({'UserProfile':{'LoginName':login,'DisplayName':name}}).encode()
  self.wfile.write(b'HTTP/1.1 200 OK\r\nContent-Length: '+str(len(data)).encode()+b'\r\n\r\n'+data)
class WhoisUnix(socketserver.ThreadingMixIn,socketserver.UnixStreamServer):
 daemon_threads=True
class Native(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.tmp=tempfile.TemporaryDirectory();cls.state=pathlib.Path(cls.tmp.name)/'state';cls.state.mkdir()
  # CI root oneshot snapshots exactly this temporary room before owner clear.
  if os.environ.get('MAG_BACKUP_CI_INTEGRATION') == '1':
   pathlib.Path('/tmp/mag-backup-ci/state-path').write_text(str(cls.state))
  cls.state.joinpath('agents.json').write_text(json.dumps([{'id':'test','name':'Test','vm':'','memory':[]}]))
  cls.whois_socket=str(pathlib.Path(cls.tmp.name)/'tailscaled.sock')
  cls.whois=WhoisUnix(cls.whois_socket,WhoisFixture)
  threading.Thread(target=cls.whois.serve_forever,daemon=True).start()
  # Stand-in for the C++20 determinism checker: records its args, reports like the real CLI.
  cls.det=pathlib.Path(cls.tmp.name)/'mag-det';cls.det.write_text('#!/bin/sh\necho "$PWD $@" >> "$(dirname "$0")/det.log"\nsleep 1\ncase "$*" in *fail-model*) echo "[MAG-FAIL] Token-Abweichung in Lauf 4"; exit 1;; esac\necho "[MAG] Konfigurations-Fingerprint: abc123"\necho "[MAG-OK] 20/20 Token-IDs und Text identisch"\necho "         Token-Stream-Hash: deadbeef"\n');cls.det.chmod(0o755)
  cls.model=http.server.ThreadingHTTPServer(('127.0.0.1',0),Model);threading.Thread(target=cls.model.serve_forever,daemon=True).start();cls.ui=port();cls.bridge=port()
  cls.start()
 @classmethod
 def start(cls):
  env=dict(os.environ,MAG_DET_CLI=str(cls.det),MAG_OWNER_CANONICAL_LOGIN='chris@github',MAG_TEST_ALLOW_LOCAL_OWNER='1',MAG_ROOM_STATE=str(cls.state),MAG_ROOM_PORT=str(cls.ui),MAG_ROOM_BRIDGE_PORT=str(cls.bridge),MAG_ROOM_BRIDGE_IP='127.0.0.1',MAG_ROOM_BACKEND=f'http://127.0.0.1:{cls.model.server_port}/v1/chat/completions',MAG_ROOM_UI=str(ROOT/'mag-room/web/index.html'),MAG_ROOM_STATIC=str(ROOT/'mag-room/static'),MAG_ROOM_AUTOTALK_SLEEP='1',MAG_JOBS_MCP_TOKEN='local-unit-test-mcp-token-0123456789',MAG_ROOM_MODEL_STATUS=str(pathlib.Path(cls.tmp.name)/'model-status.json'),MAG_NETMODE=str(NETMODE),MAG_TEST_NO_MEMGIT='1',MAG_TEST_NO_CLEAR_BACKUP='1',MAG_JOBS_ALLOW_SECONDS='1',MAG_JOBS_TICK_MS='200',MAG_JOBS_BACKOFF_S='1',MAG_GITHUB_API=f'http://127.0.0.1:{GHPORT}',MAG_TAILSCALED_SOCKET=cls.whois_socket,MAG_ROOM_MCP_AUTO='0')
  cls.log=open(pathlib.Path(cls.tmp.name)/'server.log','a');cls.proc=subprocess.Popen([str(ROOT/'build/magroom')],env=env,stdout=cls.log,stderr=cls.log)
  for _ in range(100):
   try:cls.request('/healthz',raw=True);return
   except Exception:time.sleep(.03)
  raise RuntimeError('Native startup failed')
 @classmethod
 def tearDownClass(cls):
  cls.proc.terminate();cls.proc.wait(timeout=5);cls.log.close();cls.model.shutdown();cls.whois.shutdown();cls.whois.server_close();cls.tmp.cleanup()
  if os.environ.get('MAG_BACKUP_CI_INTEGRATION') == '1':
   pathlib.Path('/tmp/mag-backup-ci/state-path').unlink(missing_ok=True)
 @classmethod
 def request(cls,path,body=None,method=None,raw=False,bridge=False,headers=None):
  data=body if isinstance(body,bytes) else json.dumps(body).encode() if body is not None else None
  headers=dict(headers or ({'Content-Type':'application/json'} if body is not None else {}))
  m=(method or ('POST' if data is not None else 'GET')).upper()
  if not bridge and m not in ('GET','HEAD') and not headers.pop('X-No-Token',None) and 'X-MAG-Token' not in headers:headers['X-MAG-Token']=_ui_token(cls.ui)
  req=urllib.request.Request(f'http://127.0.0.1:{cls.bridge if bridge else cls.ui}'+path,data=data,method=method,headers=headers)
  # /api/room/clear MUST finish the encrypted local restic snapshot before
  # acknowledging destructive changes. NUMA/CPUQuota throttling makes the
  # ~10-second CI backup exceed the normal 10s HTTP timeout.
  # Keep the tight timeout for all unrelated API calls.
  timeout=10
  with urllib.request.urlopen(req,timeout=timeout) as r:
   data=r.read();return data if raw else json.loads(data)
 def error(self,code,*args,**kw):
  with self.assertRaises(urllib.error.HTTPError) as ctx:self.request(*args,**kw)
  self.assertEqual(ctx.exception.code,code)
 def wait_idle(self):
  for _ in range(100):
   s=self.request('/api/state')
   if not s['busy']:return s
   time.sleep(.05)
  self.fail('Scheduler failed to become idle')
 def test_01_state_ui_and_readiness(self):
  self.assertEqual(self.request('/api/state')['runtime'],'C++17');self.assertTrue(self.request('/readyz')['ready'])
  self.assertIn(b'Privater Raum',self.request('/',raw=True));self.assertIn(b'function',self.request('/static/marked.min.js',raw=True))
 def test_01a_mutating_api_requires_ui_token(self):
  self.error(403,'/api/stop',{},headers={'Content-Type':'application/json','X-No-Token':'1'})
  self.error(403,'/api/stop',{},headers={'Content-Type':'application/json','X-MAG-Token':'x'*43})
  self.error(403,'/api/reset',{},headers={'Content-Type':'application/json','X-No-Token':'1'})
  self.error(403,'/api/connectors/github',{'token':'ghp_'+'a'*36},headers={'Content-Type':'application/json','X-No-Token':'1'})
  self.request('/api/stop',{});self.request('/api/queue/resume',{})
  self.request('/api/stop',{},headers={'Content-Type':'application/json','X-No-Token':'1','Sec-Fetch-Site':'same-origin'});self.request('/api/queue/resume',{})
  self.error(403,'/api/stop',{},headers={'Content-Type':'application/json','X-No-Token':'1','Sec-Fetch-Site':'cross-site'})
  html=self.request('/',raw=True).decode()
  self.assertIn('rel="icon" href="/favicon.svg?v=',html);self.assertIn('rel="icon" href="/favicon.ico?v=',html)
  for bad in ['confirm(','alert(','prompt(','Eigene Modelle','Montserrat','fonts.googleapis']:self.assertNotIn(bad,html)
  for f,ct in [('/favicon.svg','image/svg+xml'),('/favicon.ico','image/x-icon'),('/static/InterVariable.woff2','font/woff2')]:
   with urllib.request.urlopen(f'http://127.0.0.1:{self.ui}'+f,timeout=10) as r:self.assertEqual(r.headers['Content-Type'].split(';')[0],ct)
 def test_01c_connector_secret_stays_server_side(self):
  self.error(400,'/api/connectors/github',{'token':'nope'})
  tok='ghp_'+'b'*36
  self.assertTrue(self.request('/api/connectors/github',{'token':tok})['connected'])
  st=self.request('/api/connectors');self.assertTrue(st['github']['connected']);self.assertNotIn(tok,json.dumps(st))
  self.assertNotIn(tok,self.request('/api/state',raw=True).decode())
  f=self.state/'connectors'/'github.token';self.assertEqual(oct(f.stat().st_mode&0o777),'0o600')
  self.request('/api/connectors/github',method='DELETE');self.assertFalse(self.request('/api/connectors')['github']['connected'])
 def test_02_chunked_model_and_selected_agents(self):
  self.request('/api/message',{'text':'Test','bots':['test']})
  for _ in range(100):
   s=self.request('/api/state')
   if any(m.get('content')=='Hallo aus dem Modell.' for m in s['room']):break
   time.sleep(.05)
  else:self.fail('Chunked model response lost')
  self.assertEqual(Model.calls[-1]['model'],'deepseek-v4-flash-0731');self.assertFalse(s['offline']);self.assertTrue(Model.calls[-1]['stream'])
 def test_03_upload_reset_preserves_memory(self):
  self.request('/api/agent/test/remember',{'fact':'Merke mich'})
  f=self.request('/api/upload?name=hello.txt',b'hello')
  self.assertEqual(self.request('/api/file/'+f['stored'],raw=True),b'hello')
  self.request('/api/reset',{})
  self.assertFalse(self.request('/api/state')['files']);self.assertEqual(self.request('/api/agents/test/memory')[0]['text'],'Merke mich')
  self.assertFalse(list(self.state.joinpath('files').iterdir()));self.error(404,'/api/file/'+f['stored'])
 def test_03a_memory_entries_edit_and_delete(self):
  mem=self.request('/api/agents/test/memory');self.assertEqual(mem[0]['text'],'Merke mich')
  self.error(409,'/api/agents/test/memory/0',{'text':'Neu','old':'Anders'},method='PUT')
  self.error(404,'/api/agents/test/memory/99',{'text':'Neu'},method='PUT')
  r=self.request('/api/agents/test/memory/0',{'text':'Geaendert','old':'Merke mich'},method='PUT')
  self.assertEqual(r['memory'][0]['text'],'Geaendert')
  stored=json.loads(self.state.joinpath('agents.json').read_text());self.assertEqual(stored[0]['memory'][0]['text'],'Geaendert')
  r=self.request('/api/agents/test/memory/0?old=Geaendert',method='DELETE');self.assertEqual(r['memory'],[])
 def test_04_bridge_auth_and_routes(self):
  self.error(401,'/api/state',bridge=True)
  token=self.state.joinpath('token').read_text().strip();h={'Authorization':'Bearer '+token}
  self.assertEqual(self.request('/api/state',bridge=True,headers=h)['model'],'deepseek-v4-flash-0731')
  self.error(403,'/api/stop',{},bridge=True,headers=h)
 def test_05_validation_origin_and_traversal(self):
  self.error(400,'/api/message',{'text':''});self.error(400,'/api/autotalk',{'on':'yes'})
  self.error(403,'/api/stop',{},headers={'Origin':'https://evil.example'})
  self.error(404,'/static/%2e%2e%2fagents.json');self.error(404,'/api/file/../../etc/passwd')
 def test_05a_deterministic_sampling_is_per_model_authorized_and_persisted(self):
  models=self.request('/api/state')['models']
  first,second=[m['id'] for m in models]
  self.assertNotEqual(first,second)
  self.assertFalse(any(m['deterministic'] for m in models))
  self.error(410,'/api/sampling',{'deterministic':True})
  for bad in ({},{'id':'invalid','deterministic':True},{'id':first},
              {'id':first,'deterministic':'yes'},
              {'id':first,'name':'  '},{'id':first,'name':'a'*41},
              {'id':first,'name':123}):
   self.error(400,'/api/model-settings',bad)
  self.error(403,'/api/model-settings',{'id':first,'deterministic':True},headers={'Content-Type':'application/json','X-No-Token':'1'})
  token=self.state.joinpath('token').read_text().strip()
  self.error(403,'/api/model-settings',{'id':first,'deterministic':True},bridge=True,headers={'Authorization':'Bearer '+token})
  result=self.request('/api/model-settings',{'id':first,'deterministic':True})
  self.assertTrue(result['models'][0]['deterministic'])
  self.assertFalse(result['models'][1]['deterministic'])
  named=self.request('/api/model-settings',{'id':second,'name':'Mein Modell'})
  self.assertEqual(named['models'][1]['label'],'Mein Modell')
  self.assertEqual([m['id'] for m in named['models']],[first,second])
  prefs=json.loads((self.state/'model-settings.json').read_text())
  self.assertTrue(prefs[first]['deterministic'])
  self.assertFalse(prefs[second]['deterministic'])
  self.assertEqual(prefs[second]['name'],'Mein Modell')
  for model_id,expected_temp,has_seed in ((first,0,True),(second,.55,False)):
   self.request('/api/model',{'id':model_id})
   count=len(Model.calls)
   self.request('/api/message',{'text':'Sampling model '+model_id,'bots':['test']})
   for _ in range(100):
    if len(Model.calls)>count:break
    time.sleep(.05)
   self.assertGreater(len(Model.calls),count)
   self.assertEqual(Model.calls[-1]['model'],model_id)
   self.assertAlmostEqual(Model.calls[-1]['temperature'],expected_temp)
   self.assertEqual('seed' in Model.calls[-1],has_seed)
   if has_seed:
    self.assertEqual(Model.calls[-1]['seed'],42)
    self.assertEqual(Model.calls[-1]['top_k'],1)
    self.assertFalse(Model.calls[-1]['cache_prompt'])
    self.assertNotIn('tools',Model.calls[-1])
    self.assertEqual(Model.calls[-1]['messages'],
                     [{'role':'user','content':'Sampling model '+model_id}])
   else:
    self.assertTrue(Model.calls[-1]['cache_prompt'])
    self.assertIn('tools',Model.calls[-1])
    self.assertGreaterEqual(len(Model.calls[-1]['messages']),2)
    self.assertIn('[Raumverlauf]',Model.calls[-1]['messages'][1]['content'])
   self.wait_idle()
  self.request('/api/model',{'id':first})
  self.request('/api/model-settings',{'id':first,'deterministic':False})
  self.request('/api/model-settings',{'id':second,'deterministic':True})
  self.assertFalse(self.request('/api/state')['models'][0]['deterministic'])
  self.assertTrue(self.request('/api/state')['models'][1]['deterministic'])
 def test_05b_stateless_identical_input_always_recomputed_without_history(self):
  self.wait_idle()
  models=self.request('/api/state')['models']
  second=models[1]['id']
  self.assertTrue(models[1]['deterministic'])
  self.request('/api/model',{'id':second})
  count=len(Model.calls)
  for _ in range(2):
   self.request('/api/message',{'text':'hi','bots':['test']})
   for _ in range(150):
    if len(Model.calls)>count:break
    time.sleep(.05)
   self.assertGreater(len(Model.calls),count)
   self.wait_idle()
   count=len(Model.calls)
  first,again=Model.calls[-2:]
  self.assertEqual(first['messages'],[{'role':'user','content':'hi'}])
  self.assertEqual(again['messages'],first['messages'])
  for call in (first,again):
   self.assertEqual(call['model'],second)
   self.assertEqual(call['temperature'],0)
   self.assertEqual(call['top_k'],1)
   self.assertEqual(call['seed'],42)
   self.assertFalse(call['cache_prompt'])
   self.assertNotIn('tools',call)
  # Full room keeps growing, but none of it enters the decoding request.
  room=self.request('/api/state')['room']
  self.assertGreaterEqual(sum(x.get('content')=='hi' for x in room),2)
  self.assertFalse(any('Raumverlauf' in str(m) for m in again['messages']))
  # Return to regular model for tests that rely on the old chat context.
  self.request('/api/model',{'id':models[0]['id']})
 def test_05c_stateless_queued_messages_do_not_merge_or_inherit_context(self):
  self.wait_idle()
  models=self.request('/api/state')['models'];second=models[1]['id']
  self.request('/api/model',{'id':second})
  Model.delay=.4
  try:
   start=len(Model.calls)
   self.request('/api/message',{'text':'eins','bots':['test']})
   for _ in range(150):
    if self.request('/api/state')['busy']:break
    time.sleep(.02)
   self.assertTrue(self.request('/api/state')['busy'])
   self.request('/api/message',{'text':'zwei','bots':['test']})
   self.request('/api/message',{'text':'drei','bots':['test']})
   for _ in range(250):
    if len(Model.calls)>=start+3 and not self.request('/api/state')['busy']:break
    time.sleep(.05)
   self.assertEqual(len(Model.calls),start+3)
   self.assertEqual([x['messages'][0]['content'] for x in Model.calls[start:start+3]],
                    ['eins','zwei','drei'])
   self.assertTrue(all(len(x['messages'])==1 and not x.get('tools') for x in Model.calls[start:start+3]))
  finally:
   Model.delay=0
   self.request('/api/model',{'id':models[0]['id']})
 def test_06_stop_interrupts_and_human_input_stays_responsive(self):
  Model.delay=3;self.request('/api/autotalk',{'on':True})
  time.sleep(.15)
  self.assertFalse(self.request('/api/state')['busy'],'Autonomy must be idle without a new task')
  start=time.monotonic();self.request('/api/message',{'text':'Ich bin auch hier','bots':['test']});self.assertLess(time.monotonic()-start,.5)
  for _ in range(100):
   if self.request('/api/state')['busy']:break
   time.sleep(.02)
  self.assertTrue(self.request('/api/state')['busy'])
  start=time.monotonic();self.request('/api/stop',{});self.request('/api/queue/resume',{});s=self.wait_idle();self.assertLess(time.monotonic()-start,1)
  self.assertFalse(s['autotalk']);Model.delay=0
 def test_07_persistence(self):
  before=self.request('/api/state');self.proc.terminate();self.proc.wait(timeout=5);self.log.close();self.start();after=self.request('/api/state')
  self.assertEqual(before['room'],after['room']);self.assertEqual(before['agents'],after['agents']);self.assertFalse(after['autotalk'])
  self.assertEqual(before['models'],after['models'])
  self.assertFalse(after['models'][0]['deterministic'])
  self.assertTrue(after['models'][1]['deterministic'])
  self.assertEqual(after['models'][1]['label'],'Mein Modell')
  self.request('/api/model-settings',{'id':after['models'][1]['id'],'deterministic':False,'name':'1 · IQ3'})
  self.assertFalse(self.request('/api/state')['models'][1]['deterministic'])
 def test_07a_queue_survives_room_restart(self):
  Model.delay=3
  self.request('/api/message',{'text':'Erste Frage','bots':['test']})
  for _ in range(100):
   if self.request('/api/state')['busy']:break
   time.sleep(.02)
  r=self.request('/api/message',{'text':'Zweite Frage','bots':['test']});self.assertTrue(r.get('queued'))
  st=self.request('/api/state');self.assertEqual(st['queue'],1)
  q=json.loads(self.state.joinpath('queue.json').read_text());self.assertIn('active',q);self.assertEqual(len(q['pending']),1)
  self.proc.kill();self.proc.wait(timeout=5);self.log.close();Model.delay=0;self.start()
  for _ in range(200):
   s=self.request('/api/state')
   if not s['busy'] and s['queue']==0 and any('Hallo aus dem Modell' in (m.get('content') or '') for m in s['room'][-2:]):break
   time.sleep(.05)
  texts=[m.get('content') or '' for m in s['room']]
  self.assertTrue(any('unterbrochen' in t for t in texts),'interrupted turn must be marked')
  self.assertEqual(s['queue'],0);self.assertFalse(s['busy'])
  # Disk flush of the drained active lease follows the public idle event.
  for _ in range(100):
   q=json.loads(self.state.joinpath('queue.json').read_text())
   if 'active' not in q:break
   time.sleep(.05)
  self.assertNotIn('active',q);self.assertEqual(q['jobs'],[])

 # ---------------- queue / "Jetzt senden"
 def _busy(self):
  for _ in range(200):
   if self.request('/api/state')['busy']:return
   time.sleep(.02)
  self.fail('never busy')
 def _settle(self,pred,n=300):
  for _ in range(n):
   s=self.request('/api/state')
   if pred(s):return s
   time.sleep(.05)
  self.fail('condition not reached: '+json.dumps([(m.get('role'),m.get('content')) for m in s['room'][-5:]])+' busy=%s'%s['busy'])
 def _slow(self,kind,steps=40):
  pieces=[]
  for i in range(steps):
   pieces.append({'reasoning_content':'denke %d '%i} if kind=='think' else 'Wort%d '%i);pieces.append({'_sleep':.1})
  return pieces
 def _roles_ok(self,msgs):
  roles=[m['role'] for m in msgs]
  self.assertEqual(roles[0],'system')
  for i,m in enumerate(msgs):
   if m['role']=='tool':self.assertEqual(msgs[i-1]['role'] in('assistant','tool'),True)
   if m['role']=='assistant' and m.get('tool_calls'):
    ids={c['id'] for c in m['tool_calls']};got={x.get('tool_call_id') for x in msgs[i+1:i+1+len(ids)]}
    self.assertEqual(ids,got,'orphaned tool call')
  self.assertEqual(roles[-1],'user')
 def test_15a_queue_while_thinking_then_auto(self):
  self.wait_idle();Model.calls.clear();Model.script=[self._slow('think',15)+['Erste Antwort.'],['Zweite Antwort.']]
  self.request('/api/message',{'text':'Q-Eins','bots':['test']});self._busy()
  r=self.request('/api/message',{'text':'Q-Zwei','bots':['test']});self.assertTrue(r['queued']);qid=r['item']['id']
  st=self.request('/api/state');self.assertEqual([p['id'] for p in st['pending']],[qid])
  self.assertFalse(any(m.get('content')=='Q-Zwei' for m in st['room']),'queued message must not enter history early')
  s=self._settle(lambda s:not s['busy'] and not s['pending'] and s['room'][-1].get('content')=='Zweite Antwort.')
  txt=[m.get('content') for m in s['room']][-4:];self.assertEqual(txt,['Q-Eins','Erste Antwort.','Q-Zwei','Zweite Antwort.'])
 def test_15b_send_now_while_thinking(self):
  self.wait_idle();Model.script=[self._slow('think'),['Sofort.']]
  self.request('/api/message',{'text':'N-Eins','bots':['test']});self._busy();time.sleep(.5)
  r=self.request('/api/message',{'text':'N-Zwei','bots':['test']});self.request('/api/queue/'+r['item']['id']+'/now',{})
  s=self._settle(lambda s:not s['busy'] and not s['pending'] and s['room'][-1].get('content')=='Sofort.')
  tail=s['room'][-4:];self.assertEqual(tail[0]['content'],'N-Eins');self.assertEqual(tail[1]['content'],'');self.assertTrue(tail[1].get('interrupted'));self.assertEqual(tail[1].get('stopped'),'system')
  self.assertEqual(tail[2]['content'],'N-Zwei')
  last=Model.calls[-1]['messages'];self._roles_ok(last);self.assertNotIn('denke',json.dumps(last),'partial reasoning must not leak into history')
  self.assertIn('(unterbrochen)',json.dumps(last,ensure_ascii=False),'the model still learns the turn was interrupted')
 def test_15c_send_now_during_output_keeps_partial_and_prefix(self):
  self.wait_idle();Model.calls.clear();Model.script=[self._slow('out'),['Danach.']]
  self.request('/api/message',{'text':'O-Eins','bots':['test']});self._busy();time.sleep(1.0)
  r=self.request('/api/message',{'text':'O-Zwei','bots':['test']});self.request('/api/queue/'+r['item']['id']+'/now',{})
  s=self._settle(lambda s:not s['busy'] and s['room'][-1].get('content')=='Danach.')
  part=s['room'][-3];self.assertTrue(part['content'].startswith('Wort0 '));self.assertTrue(part.get('interrupted'));self.assertNotIn('(unterbrochen)',part['content'])
  a,b=Model.calls[-2]['messages'],Model.calls[-1]['messages']
  ja,jb=json.dumps(a[:1]),json.dumps(b[:1]);self.assertEqual(ja,jb,'system prompt must stay identical (cache prefix)')
  ua=a[-1]['content'];ub=b[-1]['content'];k=ua.find('[Kontext]') if '[Kontext]' in ua else len(ua)
  self.assertTrue(ub.startswith(ua[:k].rstrip()[:max(0,k-1)]),'history must only grow at its end')
  self._roles_ok(b)
 def test_15d_send_now_during_tool_call(self):
  self.wait_idle();Model.calls.clear()
  Model.script=[[{'tool_calls':[{'index':0,'id':'call_q1','function':{'name':'remember','arguments':'{"fact":"Queue-Tool"}'}}]}],self._slow('out'),['Nach Tool.']]
  self.request('/api/message',{'text':'T-Eins','bots':['test']});self._busy()
  for _ in range(100):
   if len(Model.calls)>=2:break
   time.sleep(.02)
  r=self.request('/api/message',{'text':'T-Zwei','bots':['test']});self.request('/api/queue/'+r['item']['id']+'/now',{})
  s=self._settle(lambda s:not s['busy'] and s['room'][-1].get('content')=='Nach Tool.')
  self.assertTrue(s['room'][-3].get('interrupted'));self._roles_ok(Model.calls[-1]['messages'])
  self.assertNotIn('call_q1',json.dumps(Model.calls[-1]['messages']),'tool calls of the interrupted turn must not be carried over')
 def test_15e_queue_edit_delete_merge_stop_and_persist(self):
  self.wait_idle();Model.script=[self._slow('out',60)]
  self.request('/api/message',{'text':'P-Eins','bots':['test']});self._busy()
  ids=[self.request('/api/message',{'text':t,'bots':['test']})['item']['id'] for t in ['P-a','P-b','P-c']]
  self.request('/api/queue/'+ids[0],{'text':'P-A'},method='PUT');self.request('/api/queue/'+ids[2],method='DELETE')
  self.assertEqual([p['text'] for p in self.request('/api/state')['pending']],['P-A','P-b'])
  self.request('/api/stop',{});s=self.wait_idle();self.assertEqual(len(s['pending']),2,'stop must not clear the queue');self.assertTrue(s['queue_paused'])
  self.proc.kill();self.proc.wait(timeout=5);self.log.close();self.start()
  s=self.request('/api/state');self.assertEqual([p['text'] for p in s['pending']],['P-A','P-b']);self.assertTrue(s['queue_paused'])
  self.request('/api/queue/merge',{});s=self.request('/api/state');self.assertEqual([p['text'] for p in s['pending']],['P-A\n\nP-b'])
  Model.script=[['Zusammen.']];self.request('/api/queue/resume',{})
  s=self._settle(lambda s:not s['busy'] and not s['pending'] and s['room'][-1].get('content')=='Zusammen.')
  self.assertEqual(s['room'][-2]['content'],'P-A\n\nP-b')
  self.request('/api/queue',method='DELETE')

 def test_15f_prefix_stable_across_web_toggle_and_memory(self):
  self.wait_idle();Model.script=[];Model.reply=None;Model.calls.clear()
  seq=[True,False,True,False]
  for i,w in enumerate(seq):
   self.request('/api/offline',{'on':not w});self.request('/api/message',{'text':'Cache-%d'%i,'bots':['test']})
   for _ in range(200):
    if len(Model.calls)>i and not self.request('/api/state')['busy']:break
    time.sleep(.05)
   if i==1:self.request('/api/agent/test/remember',{'fact':'Zwischendurch gemerkt'})
  c=Model.calls[-4:];trims=0
  for a,b in zip(c,c[1:]):
   self.assertEqual(_rules(a['messages'][0]['content']),_rules(b['messages'][0]['content']),'system rules must be byte-identical')
   ma,mb=_mem(a['messages'][0]['content']),_mem(b['messages'][0]['content'])
   self.assertTrue(ma==mb or mb.startswith(ma) or ma.startswith(mb),'memory block must stay identical or change only from the edit point')
   if ma==mb:self.assertEqual(a['messages'][0]['content'],b['messages'][0]['content'])
   self.assertEqual(json.dumps(a.get('tools')),json.dumps(b.get('tools')),'tool list must be byte-identical')
   ua=a['messages'][1]['content'];ub=b['messages'][1]['content'];k=ua.index('\n[Kontext]')
   if ua.split('\n')[1]!=ub.split('\n')[1]:trims+=1;continue  # planned block trim (HISTORY_STEP)
   d=next((j for j in range(k) if j>=len(ub) or ua[j]!=ub[j]),k);self.assertEqual(d,k,'transcript may only grow at its end: '+repr(ua[max(0,d-150):d+80])+' VS '+repr(ub[max(0,d-150):d+80]))
  self.assertLessEqual(trims,1)
  self.assertIn('Netz: OFFLINE',c[-1]['messages'][1]['content']);self.request('/api/offline',{'on':False})
  self.assertTrue(any(t['function']['name']=='web_search' for t in c[-1]['tools']))

 def test_15g_stop_holds_queue_but_never_blocks_new_messages(self):
  self.wait_idle();Model.script=[self._slow('out',60)];self.request('/api/autotalk',{'on':True})
  self.request('/api/message',{'text':'S-Eins','bots':['test']});self._busy()
  self.request('/api/message',{'text':'S-Zwei','bots':['test']})
  self.request('/api/stop',{});s=self.wait_idle();self.assertTrue(s['queue_paused']);self.assertFalse(s['autotalk'])
  self.assertTrue(s['pending'][0].get('held'),'items queued before Stop are held')
  self.proc.kill();self.proc.wait(timeout=5);self.log.close();self.start()
  n=len(Model.calls);time.sleep(2.5);s=self.request('/api/state')
  self.assertTrue(s['queue_paused'],'pause survives restart');self.assertEqual(len(Model.calls),n,'held items never run by themselves')
  Model.script=[['Drei.']];r=self.request('/api/message',{'text':'S-Drei','bots':['test']})
  self.assertFalse(r.get('queued'),'a new message is never blocked by a Stop')
  s=self._settle(lambda s:not s['busy'] and s['room'][-1].get('content')=='Drei.')
  self.assertEqual([p['text'] for p in s['pending']],['S-Zwei'],'held item still waits');self.assertTrue(s['queue_paused'])
  Model.script=[];self.request('/api/queue/resume',{})
  s=self._settle(lambda s:not s['busy'] and not s['pending'] and s['room'][-2].get('content')=='S-Zwei')
  self.assertFalse(s['queue_paused']);self.request('/api/autotalk',{'on':False})
 def test_15h_offline_is_enforced_and_persistent(self):
  self.wait_idle();NETLOG.unlink(missing_ok=True)
  self.request('/api/offline',{'on':True});self.assertEqual(NETLOG.read_text().split(),['offline'])
  s=self.request('/api/state');self.assertTrue(s['offline'])
  Model.calls.clear();self.request('/api/message',{'text':'Off-1','bots':['test']})
  self._settle(lambda s:not s['busy'] and len(Model.calls)>0)
  u=Model.calls[-1]['messages'][1]['content'];self.assertIn('Netz: OFFLINE',u.split('[Kontext]')[-1])
  self.proc.kill();self.proc.wait(timeout=5);self.log.close();self.start()
  self.assertEqual(NETLOG.read_text().split()[-1],'offline','offline re-applied at startup')
  # 20 quick toggles, also while a reply streams: no errors, state stays consistent
  Model.script=[self._slow('out',30)];self.request('/api/message',{'text':'Off-busy','bots':['test']});self._busy()
  import concurrent.futures as cf
  with cf.ThreadPoolExecutor(4) as ex:list(ex.map(lambda i:self.request('/api/offline',{'on':i%2==0}),range(20)))
  self.request('/api/offline',{'on':True});self.assertEqual(NETLOG.read_text().split()[-1],'offline');self.assertTrue(self.request('/api/state')['offline'])
  self.assertTrue(self.request('/api/state')['busy'],'reply keeps running while toggling');self.wait_idle()
  self.assertNotIn('(unterbrochen)',self.request('/api/state')['room'][-1]['content']);self.assertTrue(self.request('/api/state')['offline'])
  self.request('/api/offline',{'on':False});self.assertEqual(NETLOG.read_text().split()[-1],'online');self.assertFalse(self.request('/api/state')['offline'])

 def test_15i_shell_never_silently_drops_input(self):
  html=self.request('/',raw=True).decode()
  for needle in ["message?.kind==='hb'","consoleHbAt","scheduleReconnect()","termPending.push(","Shell-Verbindung verloren"]:self.assertIn(needle,html)
  go=(ROOT/'mag-room/webrtc/main.go').read_text();self.assertIn('{"kind":"hb"',go)
  self.assertIn('18840-18890',(ROOT/'ops/magbot-netmode').read_text(),'offline must keep local WebRTC ICE ports')

 # ---------------- Jobs
 def _job(self,**kw):
  b={'name':'J','prompt':'Sag Job-OK','schedule':{'type':'none'}};b.update(kw);return self.request('/api/jobs',b)
 def _jget(self,jid):return self.request('/api/jobs/'+jid,method='GET')
 def _jwait(self,jid,pred,n=200):
  for _ in range(n):
   j=self._jget(jid)
   if pred(j):return j
   time.sleep(.05)
  self.fail('job condition not reached: '+json.dumps(j)[:500])
 def _restart(self):
  self.proc.kill();self.proc.wait(timeout=5);self.log.close();self.start()
 def test_16a_job_runs_on_schedule_as_own_run(self):
  self.wait_idle();Model.script=[];Model.reply='Job erledigt.';Model.calls.clear()
  j=self._job(schedule={'type':'interval','every':1,'unit':'s'})
  self._jwait(j['id'],lambda x:x['last_status']=='ok')
  self.request('/api/jobs/'+j['id'],method='DELETE');Model.reply=None
  s=self.wait_idle();self.assertTrue(any((m.get('content') or '').startswith('🗓 Job „J“') for m in s['room']))
  c=[x for x in Model.calls if x['messages'][1]['content'].startswith('[Geplanter Job')][0]
  self.assertIn('Sag Job-OK',c['messages'][1]['content']);self.assertNotIn('Sag Job-OK',c['messages'][0]['content'],'job prompt never in system prompt')
 def test_16b_no_double_run_and_webhook_auth(self):
  self.wait_idle();Model.script=[self._slow('out',15)]
  j=self._job(webhook=True);sec=j['secret'];self.assertNotIn('secret',self._jget(j['id']));self.assertNotIn('hook_hash',json.dumps(self._jget(j['id'])))
  r1=self.request('/api/jobs/'+j['id']+'/run',{});self.error(409,'/api/jobs/'+j['id']+'/run',{})
  hdr={'Content-Type':'application/json','X-No-Token':'1'}
  self.error(401,'/hook/jobs/'+j['id'],{},headers=dict(hdr,**{'X-MAG-Job-Secret':'falsch'}))
  self.error(409,'/hook/jobs/'+j['id'],{},headers=dict(hdr,**{'X-MAG-Job-Secret':sec}))
  self._jwait(j['id'],lambda x:not x['running'] and x['last_status']=='ok',400)
  r=self.request('/hook/jobs/'+j['id'],{},headers=dict(hdr,**{'X-MAG-Job-Secret':sec}));self.assertTrue(r['accepted'])
  x=self._jwait(j['id'],lambda x:len(x['history'])==2,400);self.assertEqual([h['reason'] for h in x['history']],['manuell','Webhook'])
  n=self.request('/api/jobs/'+j['id']+'/secret',{})['secret'];self.assertNotEqual(n,sec)
  self.error(401,'/hook/jobs/'+j['id'],{},headers=dict(hdr,**{'X-MAG-Job-Secret':sec}))
  self.request('/api/jobs/'+j['id'],method='DELETE')
 def test_16c_restart_catch_up_exactly_once_and_lease(self):
  self.wait_idle();Model.script=[];Model.reply='Nachgeholt.'
  j=self._job(schedule={'type':'interval','every':1,'unit':'h'});k=self._job(name='K')
  self._restart();self.proc.kill();self.proc.wait(timeout=5);self.log.close()
  f=self.state/'jobs.json';data=json.loads(f.read_text())
  for d in data:
   if d['id']==j['id']:d['next_run']=time.time()-5*3600  # 5 missed periods
   if d['id']==k['id']:d['queued_run']=k['id']+':lease-1'   # crashed mid-run
  f.write_text(json.dumps(data));self.start()
  x=self._jwait(j['id'],lambda x:len(x['history'])>=1);time.sleep(1.5);x=self._jget(j['id'])
  self.assertEqual(len(x['history']),1,'catch-up exactly once');self.assertGreater(x['next_run'],time.time()+3000)
  y=self._jwait(k['id'],lambda y:len(y['history'])>=1);time.sleep(.5)
  self.assertEqual([h['run_id'] for h in self._jget(k['id'])['history']],[k['id']+':lease-1'])
  self._restart();time.sleep(1.5);self.assertEqual(len(self._jget(k['id'])['history']),1,'no double run after restart')
  for i in (j,k):self.request('/api/jobs/'+i['id'],method='DELETE')
  Model.reply=None
 def test_16d_edit_and_delete_while_running(self):
  self.wait_idle();Model.calls.clear();Model.script=[self._slow('out',15),['Neu.']]
  j=self._job(prompt='ALT-PROMPT');self.request('/api/jobs/'+j['id']+'/run',{});self._busy()
  self.request('/api/jobs/'+j['id'],{'prompt':'NEU-PROMPT','name':'J2'},method='PUT')
  x=self._jwait(j['id'],lambda x:len(x['history'])==1,400);self.assertEqual(x['history'][0]['version'],1);self.assertEqual(x['version'],2)
  self.request('/api/jobs/'+j['id']+'/run',{});x=self._jwait(j['id'],lambda x:len(x['history'])==2,400)
  self.assertIn('NEU-PROMPT',Model.calls[-1]['messages'][1]['content']);self.assertIn('ALT-PROMPT',Model.calls[-2]['messages'][1]['content'])
  Model.script=[self._slow('out',15)];self.request('/api/jobs/'+j['id']+'/run',{});self._busy()
  self.request('/api/jobs/'+j['id'],method='DELETE');self.wait_idle();n=len(Model.calls);time.sleep(1.5)
  self.assertEqual(len(Model.calls),n,'no further run after delete');self.error(404,'/api/jobs/'+j['id'],method='GET')
 def test_16e_retry_backoff_then_error(self):
  self.wait_idle();Model.script=[[''],[''],['']];Model.reply=''
  j=self._job();self.request('/api/jobs/'+j['id']+'/run',{})
  x=self._jwait(j['id'],lambda x:x['last_status']=='error',300)
  self.assertEqual(len(x['history']),3);self.assertTrue(x['history'][1]['run_id'].endswith('#r2'));self.assertIn('3 Versuchen',x['last_error'])
  Model.reply=None;Model.script=[];self.request('/api/jobs/'+j['id'],method='DELETE')
 def test_16f_jobs_respect_stop_pause(self):
  self.wait_idle();Model.reply='P.';self.request('/api/stop',{})
  j=self._job();self.request('/api/jobs/'+j['id']+'/run',{});n=len(Model.calls);time.sleep(1.5)
  self.assertEqual(len(Model.calls),n,'no job runs while paused');self.assertTrue(self._jget(j['id'])['running'])
  self.request('/api/queue/resume',{});self._jwait(j['id'],lambda x:x['last_status']=='ok')
  html=self.request('/',raw=True).decode()
  for n in ['id="job-dialog"','Jetzt ausführen','jf-secret','/api/jobs']:self.assertIn(n,html)
  self.assertNotIn('confirm(',html.replace('magConfirm(',''))
  self.request('/api/jobs/'+j['id'],method='DELETE');Model.reply=None

 def test_15k_queue_move_persists_and_broadcasts(self):
  self.wait_idle();Model.script=[self._slow('out',80)]
  self.request('/api/message',{'text':'Sort-Lauf','bots':['test']});self._busy()
  try:
   created=[self.request('/api/message',{'text':t,'bots':['test']})['item']
            for t in ['Sort-A','Sort-B','Sort-C']]
   ids=[p['id'] for p in created]
   self.assertEqual([p['id'] for p in self.request('/api/queue',method='GET')['pending']],ids)
   self.request('/api/queue/'+ids[2]+'/move',{'before':ids[0]})
   expected=[ids[2],ids[0],ids[1]]
   self.assertEqual([p['id'] for p in self.request('/api/queue',method='GET')['pending']],expected)
   self.assertEqual([p['id'] for p in json.loads((self.state/'queue.json').read_text())['pending']],expected)
   self.request('/api/queue/'+ids[2]+'/move',{'before':''})
   self.assertEqual([p['id'] for p in self.request('/api/queue',method='GET')['pending']],[ids[0],ids[1],ids[2]])
   self.error(404,'/api/queue/'+ids[0]+'/move',{'before':'not-a-job'})
   html=self.request('/',raw=True).decode()
   for needle in ("queueDropAt(","queueShift(","dragstart","pointerup","/move","photo-preview","upload-photo"):
    self.assertIn(needle,html)
   self.assertIn("btn.addEventListener('wheel'",html)
   self.assertNotIn("btn.addEventListener('wheel',e=>{e.preventDefault()",html)
  finally:
   self.request('/api/queue',method='DELETE');self.request('/api/cancel',{});self.request('/api/queue/resume',{});Model.script=[]

 def test_15k_composer_stop_cancels_turn_keeps_queue(self):
  self.wait_idle();Model.script=[self._slow('out',60),['Weiter normal.']]
  self.request('/api/message',{'text':'Stop-Test','bots':['test']});self._busy();time.sleep(1.0)
  self.assertEqual(self.request('/api/cancel',{})['cancelled'],True)
  s=self.wait_idle();self.assertFalse(s.get('queue_paused'),'composer stop must not pause the queue')
  part=s['room'][-1];self.assertTrue(part['content'].startswith('Wort0 '));self.assertNotIn('(abgebrochen)',part['content']);self.assertTrue(part.get('interrupted'))
  self.assertEqual(part.get('stopped'),'user');self.assertNotIn('work_error',part)
  self.assertEqual(self.request('/api/cancel',{})['cancelled'],False,'idle cancel is a no-op')
  self.request('/api/message',{'text':'Danach','bots':['test']});self.wait_room('Weiter normal.');s=self.wait_idle()
  self.assertIn('(abgebrochen)',json.dumps(Model.calls[-1]['messages'],ensure_ascii=False),'the model still learns the answer was stopped')
  self.assertEqual(s['room'][-1]['content'],'Weiter normal.')
 def test_15j_reload_mid_stream_replays_and_continues(self):
  self.wait_idle();Model.script=[self._slow('out',25)]
  self.request('/api/message',{'text':'Stream-Reload','bots':['test']});self._busy();time.sleep(1.0)
  r=urllib.request.urlopen(f'http://127.0.0.1:{self.ui}/api/events',timeout=10)
  ev=[];text='';draft='';t0=time.time()
  while time.time()-t0<8:
   line=r.readline().decode()
   if not line.startswith('data: '):continue
   e=json.loads(line[6:]);ev.append(e.get('type'))
   if e.get('type')=='delta':text+=e.get('text','')
   if e.get('type')=='draft':draft+=e.get('text','')
   if e.get('type') in('bot_done','idle'):break
  r.close()
  self.assertEqual(ev[0],'bot_start','replay starts the running turn');self.assertIn([e for e in ev[1:] if e!='step'][0],('delta','draft'),'status steps may precede the replayed text')
  self.assertEqual(text,''.join('Wort%d '%i for i in range(25)),'replay + live = exact answer, no gap/duplicate')
  self.assertEqual(draft,text,'replayed draft + live drafts = exact answer, no gap/duplicate')
  s=self.wait_idle();self.assertEqual(s['room'][-1]['content'].strip(),text.strip())
  r=urllib.request.urlopen(f'http://127.0.0.1:{self.ui}/api/events',timeout=3)
  try:
   r.fp.raw._sock.settimeout(1.0);line=r.readline().decode()
  except Exception:line=''
  r.close();self.assertNotIn('bot_start',line,'no replay after the turn finished')

 def test_16g_github_tool_server_side_token(self):
  seen=[]
  class GH(http.server.BaseHTTPRequestHandler):
   def log_message(self,*a):pass
   def do_GET(self):
    seen.append((self.path,self.headers.get('Authorization')));b=json.dumps([{'full_name':'chris/mag','private':True,'description':'x','extra':'drop'}]).encode()
    self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b)
  srv=http.server.ThreadingHTTPServer(('127.0.0.1',GHPORT),GH);threading.Thread(target=srv.serve_forever,daemon=True).start()
  try:
   self.wait_idle();call={'tool_calls':[{'index':0,'id':'call_gh1','function':{'name':'github','arguments':'{"action":"list_repos"}'}}]}
   Model.calls.clear();Model.script=[[call],['Keine Verbindung.']]
   self.request('/api/message',{'text':'GH-1','bots':['test']});self.wait_idle();time.sleep(.3)
   self.assertTrue(any(t['function']['name']=='github' for t in Model.calls[0]['tools']),'github always registered')
   self.assertIn('nicht verbunden',json.dumps(Model.calls[-1]['messages']))
   self.request('/api/connectors/github',{'token':'ghp_testtoken1234567890'})
   Model.calls.clear();Model.script=[[call],['Repos gelistet.']]
   self.request('/api/message',{'text':'GH-2','bots':['test']});self.wait_idle();time.sleep(.3)
   msgs=json.dumps(Model.calls[-1]['messages'])
   self.assertIn('chris/mag',msgs);self.assertNotIn('extra',msgs);self.assertNotIn('ghp_testtoken1234567890',msgs)
   self.assertEqual(seen[-1],('/user/repos?per_page=50&sort=updated','Bearer ghp_testtoken1234567890'))
   self.assertFalse([p for p in (self.state/'connectors').iterdir() if p.name.startswith('.gh-hdr')],'header file removed')
   self.request('/api/connectors/github',method='DELETE')
  finally:srv.shutdown()
 def test_16h_s23_tool_does_not_touch_s23pub_when_unconfigured(self):
  self.wait_idle()
  call={'tool_calls':[{'index':0,'id':'call_s23','function':{'name':'s23_status','arguments':'{}'}}]}
  Model.calls.clear();Model.script=[[call],['S23 nicht gestartet.']]
  self.request('/api/message',{'text':'S23-Status','bots':['test']});self.wait_idle();time.sleep(.2)
  self.assertTrue(any(t['function']['name']=='s23_status' for t in Model.calls[0]['tools']))
  blob=json.dumps(Model.calls[-1]['messages'])
  self.assertIn('nicht konfiguriert',blob)
  self.assertIn('s23pub',blob)
  self.assertNotIn('8720',blob)
 def test_08_idle_autonomy_does_not_make_up_work(self):
  self.request('/api/autotalk',{'on':True})
  calls_before=len(Model.calls)
  time.sleep(1.2)
  self.assertEqual(len(Model.calls),calls_before,'Autonomy should not run without new user work')
  self.assertTrue(self.request('/api/state')['autotalk'],'Keep the enabled switch ON')
  self.request('/api/stop',{});self.request('/api/queue/resume',{})
 def test_09_task_without_tools_stops_after_one_answer(self):
  Model.reply='Erledigt. [[MAG:ENDE]]'
  try:
   self.request('/api/autotalk',{'on':True})
   initial=len(Model.calls)
   self.request('/api/message',{'text':'Pruefe einen Sachverhalt','bots':['test']})
   for _ in range(100):
    state=self.request('/api/state')
    if len(Model.calls)>initial and any(m.get('content')=='Erledigt.' for m in state['room']):break
    time.sleep(.05)
   else:self.fail('Expected model answer without visible control marker')
   time.sleep(1.2)
   self.assertEqual(len(Model.calls),initial+1,'No tools: never trigger an automatic continuation')
   self.assertTrue(state['autotalk'])
  finally:
   self.request('/api/stop',{});self.request('/api/queue/resume',{});Model.reply=None
 # ---- DeepSeek-V4-Flash-0731 integration -------------------------------------------------
 def wait_room(self,needle,loops=240):
  for _ in range(loops):
   s=self.request('/api/state')
   if any(needle in m.get('content','') for m in s['room']):return s
   time.sleep(.05)
  self.fail('Room message not found: '+needle)
 def test_09a_deepseek_request_parameters(self):
  self.request('/api/message',{'text':'Parameter','bots':['test']})
  self.wait_room('Hallo aus dem Modell.');self.wait_idle()
  call=Model.calls[-1]
  self.assertNotIn('reasoning_effort',call,'GPT-OSS top-level reasoning_effort must be gone')
  # reasoning_effort would put a preamble BEFORE the system prompt (cache miss) -> not sent by default
  self.assertEqual(call['chat_template_kwargs'],{'enable_thinking':True})
  self.assertEqual(call['max_tokens'],8192)
  self.assertIn('deepseek-v4-flash-0731',call['messages'][0]['content'])
  self.assertNotIn('gpt-oss',json.dumps(call))
 def test_09o_context_overflow_retries_same_step_without_losing_task(self):
  self.wait_idle();Model.script=[];Model.reply=None
  start=len(Model.calls);Model.overflow_once=1
  try:
   self.request('/api/message',{'text':'Kontext-Retry-Auftrag','bots':['test']})
   self._settle(lambda s:not s['busy'] and len(Model.calls)>=start+2 and
                s['room'][-1].get('content')=='Hallo aus dem Modell.')
   self.assertEqual(len(Model.calls),start+2,'Only the same model step must be retried')
   self.assertIn('Kontext-Retry-Auftrag',json.dumps(Model.calls[-1]['messages'],ensure_ascii=False))
   self.assertFalse(any(m.get('work_error','').startswith('Backend HTTP 400') for m in self.request('/api/state')['room'][-2:]))
  finally:
   Model.overflow_once=0
 def test_09p_long_room_is_bounded_and_preserves_latest_instruction(self):
  self.wait_idle();Model.script=[];Model.reply='OK-Kontext'
  retained=[]
  try:
   for i in range(7):
    marker=f'kontext-lang-{i}-'
    before=len(Model.calls)
    self.request('/api/message',{'text':marker+'Z'*4800,'bots':['test']})
    self._settle(lambda s:not s['busy'] and len(Model.calls)>before and
                 s['room'][-1].get('content')=='OK-Kontext')
    retained.append(Model.calls[-1]['messages'][1]['content'].split('[Kontext]')[0])
   call=Model.calls[-1]
   transcript=call['messages'][1]['content'].split('[Kontext]')[0]
   self.assertLessEqual(len(transcript.encode()),16020,'Room transcript must have a byte cap')
   self.assertIn('kontext-lang-6-',transcript)
   self.assertEqual(call['model'],'deepseek-v4-flash-0731')
   comparison=[(len(a),len(b),len(os.path.commonprefix([a,b])),a[:80]==b[:80])
               for a,b in zip(retained,retained[1:])]
   # Before append, the last line ends with a trailing newline. When the
   # next turn arrives, the boundary newline may be reflowed; that one byte
   # does not mean the thousands of preceding bytes became uncached.
   # Require multiple large common prefixes, not a false exact startswith.
   self.assertGreaterEqual(
       sum(len(a)>2000 and len(os.path.commonprefix([a,b]))>=len(a)-2
           for a,b in zip(retained,retained[1:])),2,
       'large room must retain multiple cacheable prefixes: '+repr(comparison))
  finally:
   Model.reply=None
 def test_09q_two_warm_models_no_single_model_fallback(self):
  self.wait_idle()
  q4='deepseek-v4-flash-0731';iq3='deepseek-v4-flash-0731-iq3'
  self.assertEqual([m['id'] for m in self.request('/api/model')['models']],[q4,iq3])
  self.error(400,'/api/model',{'id':'unknown'})

 def test_09b_reasoning_is_returned_with_tool_history(self):
  # A previous test may leave a background request; drain before injecting
  # the two scripted responses to avoid racy Model.script consumption.
  self.request('/api/autotalk',{'on':False})
  self.wait_idle()
  first=[{'reasoning_content':'Ich merke mir das.'},{'tool_calls':[{'index':0,'id':'call_ds_1','function':{'name':'remember','arguments':'{"fact":"DeepSeek-Test"}'}}]}]
  Model.script=[first,['Notiert.']]
  try:
   initial=len(Model.calls)
   self.request('/api/message',{'text':'Merke DeepSeek-Test','bots':['test']})
   self.wait_room('Notiert.');self.wait_idle()
   # Assert this test's tool roundtrip, not unrelated global Model.calls.
   fresh=Model.calls[initial:]
   own=[x for x in fresh if any('Merke DeepSeek-Test' in (msg.get('content') or '')
                             for msg in x.get('messages',[]) if isinstance(msg.get('content'),str))]
   self.assertEqual(len(own),2,repr([x.get('messages',[])[-1:] for x in fresh])[:1200])
   msgs=own[-1]['messages']
   asst=[m for m in msgs if m['role']=='assistant' and m.get('tool_calls')]
   self.assertEqual(asst[-1]['reasoning_content'],'Ich merke mir das.')
   self.assertEqual(asst[-1]['tool_calls'][0]['function']['name'],'remember')
   tool=[m for m in msgs if m['role']=='tool']
   self.assertEqual(tool[-1]['tool_call_id'],'call_ds_1')
  finally:Model.script=[]
 def test_09c_model_status_ready(self):
  st=self.request('/api/model-status')
  self.assertTrue(st['ready']);self.assertEqual(st['state'],'ready');self.assertEqual(st['model'],'deepseek-v4-flash-0731')
  self.assertTrue(self.request('/readyz/model')['ready'])
  self.assertIn('model_status',self.request('/api/state'))
 def test_09d_download_in_progress_is_reported_not_called(self):
  status=pathlib.Path(self.tmp.name)/'model-status.json'
  status.write_text(json.dumps({'state':'downloading','detail':'Teil 4/5: 61 %'}))
  Model.health=502
  try:
   st=self.request('/api/model-status')
   self.assertFalse(st['ready']);self.assertEqual(st['state'],'downloading');self.assertIn('heruntergeladen',st['label'])
   self.assertTrue(self.request('/readyz')['ready'],'Room readiness must not depend on the model (deploy gate)')
   self.error(503,'/readyz/model')
   initial=len(Model.calls)
   self.request('/api/message',{'text':'Bist du da?','bots':['test']})
   self.wait_room('Modell wird heruntergeladen');self.wait_idle()
   self.assertEqual(len(Model.calls),initial,'No inference request while the model is not available')
  finally:
   Model.health=200;status.unlink()
 def test_09e_turn_waits_while_model_loads(self):
  Model.health=503
  def ready_later():
   time.sleep(2.5);Model.health=200
  threading.Thread(target=ready_later,daemon=True).start()
  start=time.monotonic()
  self.request('/api/message',{'text':'Warte bitte','bots':['test']})
  for _ in range(300):
   s=self.request('/api/state')
   if not s['busy'] and any(m.get('content')=='Hallo aus dem Modell.' and m.get('ts',0)>=0 for m in s['room'][-1:]):break
   time.sleep(.05)
  else:self.fail('Turn did not continue after the model became ready')
  self.assertGreater(time.monotonic()-start,2.0,'Must wait while the model is loading')
 def test_09f_chat_only_bot_without_vm(self):
  a=self.request('/api/agents',{'name':'DeepSeek Chat','vm':False})
  try:
   self.assertEqual(a['vm'],'');self.assertEqual(a['id'],'deepseek-chat')
   self.assertIn('deepseek-chat',[x['id'] for x in self.request('/api/state')['agents']])
   # No browser/console for chat-only bots: clear 400 instead of "Invalid browser VM identity"
   with self.assertRaises(urllib.error.HTTPError) as ctx:
    self.request('/api/browser-live/offer?bot=deepseek-chat',{'type':'offer','sdp':'x'})
   self.assertEqual(ctx.exception.code,400)
   self.assertIn('Nur-Chat-Bot',json.loads(ctx.exception.read())['error'])
   self.error(400,'/api/screen/deepseek-chat')
   self.error(400,'/api/browser/deepseek-chat/read',{})
  finally:
   self.assertTrue(self.request('/api/agents/deepseek-chat',method='DELETE')['ok'])
 def test_09g_frontend_model_status_contract(self):
  html=self.request('/',raw=True).decode()
  for needle in ('id="model-state"','id="model-banner"',"event.type==='model'","event.type==='bot_status'",'id="agent-chat-only"','vm:false',
                 "if(!a.vm)continue","if(!bot.vm){","showThinking(s.node","#model-state .txt{display:none}"):
   self.assertIn(needle,html)
 def test_09h_prompt_is_cache_friendly(self):
  # Stable rules, memories directly after them, volatile clock/files only in the [Kontext] tail.
  def turn(text):
   n=len(self.request('/api/state')['room']);calls=len(Model.calls)
   self.request('/api/message',{'text':text,'bots':['test']})
   self._settle(lambda s: not s['busy'] and len(Model.calls)>calls and len(s['room'])>n)
   return n,Model.calls[-1]['messages']
  n1,m1=turn('Cache eins')
  self.request('/api/agent/test/remember',{'fact':'Neue Erinnerung zwischen zwei Runden'})
  n2,m2=turn('Cache zwei')
  self.assertEqual(_rules(m1[0]['content']),_rules(m2[0]['content']),'System rules must not change between turns')
  self.assertNotIn('Neue Erinnerung',_rules(m2[0]['content']))
  mem1,mem2=_mem(m1[0]['content']),_mem(m2[0]['content'])
  self.assertTrue(mem2.startswith(mem1),'New memory is appended; the previous block stays a prefix')
  self.assertTrue(mem2.endswith('- Neue Erinnerung zwischen zwei Runden'))
  self.assertNotIn('Neue Erinnerung',m2[1]['content'])
  self.assertIn('[Kontext]',m2[1]['content']);self.assertIn('Zeit:',m2[1]['content'].split('[Kontext]')[1])
  self.assertNotIn('Zeit:',_rules(m2[0]['content']))
  start=lambda n:((n+1-30)//10)*10 if n+1>30 else 0   # +1: the new user message
  if start(n1)==start(n2):
   self.assertTrue(m2[1]['content'].startswith(m1[1]['content'].split('\n[Kontext]')[0]),'Transcript must only grow at its end')
  self.assertTrue(Model.calls[-1]['return_progress']);self.assertTrue(Model.calls[-1]['cache_prompt'])
  self.assertNotIn('id_slot',Model.calls[-1],'one llama slot: the server must pick it so --cache-ram applies')
 def test_09n_prompt_cache_reuses_identical_prefix(self):
  # Fake llama: tokens_cached is the shared prefix. Identical
  # memory bytes must make the second turn a cache hit. Appending a memory
  # keeps the old block as a prefix; an edit rewrites only from that line.
  Model.last_prompt.clear()
  self.request('/api/agent/test/remember',{'fact':'Alpha bleibt'})
  self.request('/api/agent/test/remember',{'fact':'Beta aendert sich'})
  def turn(text):
   calls=len(Model.calls)
   self.request('/api/message',{'text':text,'bots':['test']})
   self._settle(lambda s: not s['busy'] and len(Model.calls)>calls and any(m.get('content')==text for m in s['room']))
   return Model.calls[-1]
  a=turn('Prefix eins');b=turn('Prefix zwei')
  self.assertTrue(a['cache_prompt'] and b['cache_prompt'])
  self.assertNotIn('id_slot',a);self.assertNotIn('id_slot',b)
  ma,mb=_mem(a['messages'][0]['content']),_mem(b['messages'][0]['content'])
  self.assertEqual(ma,mb,'unchanged memory must be byte-identical')
  self.assertEqual(hashlib.sha256(ma.encode()).hexdigest(),hashlib.sha256(mb.encode()).hexdigest())
  st=self.request('/api/state')['model_info']['last']
  self.assertGreater(st['tokens_evaluated'],0)
  self.assertGreaterEqual(st['tokens_cached']*2,st['tokens_evaluated'],'second identical-prefix request must reuse the cache')
  html=self.request('/',raw=True).decode()
  self.assertIn("txt+=' · Prompt '",html)
  self.assertIn('Cache (Debug)',html)
  self.assertNotIn("txt+=' · Cache'",html)
  before=mb
  self.request('/api/agent/test/remember',{'fact':'Gamma neu'})
  c=turn('Prefix drei');after=_mem(c['messages'][0]['content'])
  self.assertTrue(after.startswith(before),'appended memory keeps the previous block as a prefix')
  self.assertTrue(after.rstrip().endswith('Gamma neu'))
  self.assertEqual(_rules(b['messages'][0]['content']),_rules(c['messages'][0]['content']))
  mem=self.request('/api/agents/test/memory')
  idx=next(i for i,x in enumerate(mem) if x.get('text')=='Beta aendert sich')
  self.request('/api/agents/test/memory/%d'%idx,{'text':'Beta geaendert','old':'Beta aendert sich'},method='PUT')
  d=turn('Prefix vier');edited=_mem(d['messages'][0]['content'])
  head=before.split('\n- Beta aendert sich')[0]
  self.assertTrue(edited.startswith(head),'edit re-renders only from the changed line')
  self.assertLess(edited.find('- Beta geaendert'),edited.find('- Gamma neu'))
  self.assertTrue(edited.rstrip().endswith('Gamma neu'))
  log=(pathlib.Path(self.tmp.name)/'server.log').read_text(errors='replace')
  self.assertIn('tokens_cached=',log);self.assertIn('tokens_evaluated=',log)
 def test_09o_bot_switch_keeps_each_prompt_cached(self):
  # Two bots alternate on one llama slot. Unpinned, the server's RAM prompt
  # cache restores each bot's own prefix; pinned, every switch was a miss.
  Model.last_prompt.clear()
  b=self.request('/api/agents',{'name':'Wechsel','vm':False})
  try:
   def turn(text,bot):
    calls=len(Model.calls)
    self.request('/api/message',{'text':text,'bots':[bot]})
    self._settle(lambda s: not s['busy'] and len(Model.calls)>calls and any(m.get('content')==text for m in s['room']))
    return self.request('/api/state')['model_info']['last']
   turn('Wechsel eins','test');turn('Wechsel zwei',b['id']);st=turn('Wechsel drei','test')
   self.assertGreaterEqual(st['tokens_cached']*2,st['tokens_evaluated'],'returning bot must hit its cached prefix')
   self.assertNotIn('id_slot',Model.calls[-1])
  finally:
   self.assertTrue(self.request('/api/agents/'+b['id'],method='DELETE')['ok'])
 def test_09p_owner_adds_memory_in_modal(self):
  r=self.request('/api/agents/test/memory',{'text':'  Manuell ergaenzt  '})
  self.assertTrue(r['ok']);self.assertEqual(r['memory'][-1]['text'],'Manuell ergaenzt')
  self.assertEqual(r['memory'][-1]['src'],'Nutzer')
  self.assertEqual(self.request('/api/agents/test/memory',method='GET')[-1]['text'],'Manuell ergaenzt')
  self.error(409,'/api/agents/test/memory',{'text':'Manuell ergaenzt'})
  self.error(400,'/api/agents/test/memory',{'text':'   '})
  self.error(400,'/api/agents/test/memory',{'text':'x'*501})
  self.error(404,'/api/agents/nope/memory',{'text':'egal'})
  html=self.request('/',raw=True).decode()
  for needle in ('id="memory-new"','id="memory-add"',"'/memory',{text},'POST'",'id="memory-err"'):self.assertIn(needle,html)
 def test_09o_model_determinism_check_runs_cli_and_shows_result(self):
  self.error(400,'/api/model-verify',{'model':'nope'})
  self.assertEqual(self.request('/api/model-verify',{'model':'deepseek-v4-flash-0731'},raw=True),b'{"ok":true}')
  self.error(409,'/api/model-verify',{'model':'deepseek-v4-flash-0731'})   # one at a time
  def verify():
   return next(m for m in self.request('/api/state')['models'] if m['id']=='deepseek-v4-flash-0731').get('verify') or {}
  for _ in range(100):
   if verify().get('state')!='running':break
   time.sleep(.1)
  v=verify();self.assertEqual((v['state'],v['runs'],v['hash'],v['fingerprint']),('ok',20,'deadbeef','abc123'))
  log=(self.det.parent/'det.log').read_text()
  self.assertIn(str(self.state),log)   # report goes to the writable state dir
  self.assertIn('--verify --host 127.0.0.1 --port '+str(self.model.server_port)+' --model deepseek-v4-flash-0731 --numa none',log)
  self.assertTrue((self.state/'model-verify.json').exists())
  html=self.request('/',raw=True).decode()
  for needle in ("'/api/model-verify'",'function paintModelVerify(',"'✓ Geprüft: 20 von 20 Antworten identisch · '"):self.assertIn(needle,html)
 def test_09p_file_ranges_and_inline_chat_attachments(self):
  # Video only seeks/plays on Safari with byte ranges; stored names are immutable.
  body=bytes(range(200))
  f=self.request('/api/upload?name=clip.mp4',body)
  self.assertEqual(f['mime'],'video/mp4')
  url=f'http://127.0.0.1:{self.ui}/api/file/'+f['stored']
  def get(rng):
   with urllib.request.urlopen(urllib.request.Request(url,headers={'Range':rng}),timeout=10) as r:return r.status,dict(r.headers),r.read()
  st,h,data=get('bytes=10-19');self.assertEqual((st,data,h['Content-Range']),(206,body[10:20],'bytes 10-19/200'))
  st,h,data=get('bytes=-5');self.assertEqual((st,data),(206,body[-5:]))
  st,h,data=get('bytes=190-');self.assertEqual((st,data),(206,body[190:]))
  self.assertIn('immutable',h['Cache-Control']);self.assertEqual(h['Accept-Ranges'],'bytes')
  with self.assertRaises(urllib.error.HTTPError) as ctx:get('bytes=500-600')
  self.assertEqual(ctx.exception.code,416)
  self.assertEqual(self.request('/api/file/'+f['stored'],raw=True),body)
  html=self.request('/',raw=True).decode()
  # Files live in the conversation (image/video inline, cards), not a side list.
  for needle in ('function attachmentView(f)','function openLightbox(','class="drop-overlay"',"v.playsInline=true","'browser.png':'Browser-Screenshot'"):self.assertIn(needle,html)
  self.assertNotIn('Gemeinsame Dateien',html)
  # The hidden live checkbox must not be restored from an old stored 'false'.
  self.assertIn("localStorage.removeItem('mag-live')",html);self.assertNotIn("['language','live']",html)
 def test_09q_pasted_screenshot_reaches_bot_as_image_file(self):
  # The browser re-encodes a pasted/dropped screenshot to JPEG and posts it
  # through the normal upload; the text-only model gets a clear image note.
  jpeg=b'\xff\xd8\xff\xe0\x00\x10JFIF\x00'+b'\x00'*64+b'\xff\xd9'
  f=self.request('/api/upload?name=screenshot.jpg',jpeg)
  self.assertEqual(f['mime'],'image/jpeg')
  self.assertEqual(self.request('/api/file/'+f['stored'],raw=True),jpeg)
  t=self.request('/api/upload?name=notiz.txt',b'text')
  s=self.request('/api/state')
  self.assertTrue(any((m.get('file') or {}).get('name')==f['name'] and m.get('role')=='user' for m in s['room']))
  self.request('/api/message',{'text':'Was steht auf dem Screenshot?','bots':['test']})
  self.wait_room('Hallo aus dem Modell.');self.wait_idle()
  user=Model.calls[-1]['messages'][1]['content']
  self.assertIn('[Bild: '+f['name']+' – du siehst es nicht direkt; Text darin mit see_image("'+f['name']+'") lesen]',user)
  self.assertIn('['+t['name']+']',user);self.assertNotIn('[Bild: '+t['name'],user)
  self.assertIn('see_image',[x['function']['name'] for x in Model.calls[-1]['tools']])
  html=self.request('/',raw=True).decode()
  for needle in ('<span>Foto/Screenshot</span>','accept="image/*" multiple','id="upload-paste"','navigator.clipboard.read()','PHOTO_MAX_PX=2048','id="photo-note"',"addEventListener('paste'","addEventListener('drop'"):self.assertIn(needle,html)
  # Native file drag: PDFs/text/archives and photos stage until send.
  for needle in ('id="file-preview"','function stageFiles(files)',
                 'function paintStagedFiles()','function stageAttachments(files)',
                 'async function flushStagedFiles()',
                 'if(stagedFiles.length)await flushStagedFiles()',
                 "for(const target of [$('send-form')",
                 'e.preventDefault();e.stopPropagation();dropActive(false);',
                 'e.dataTransfer?.files',
                 'stageAttachments([...(e.target.files||[])])',
                 'if(!text&&!stagedPhotos.length&&!stagedFiles.length)return'):
   self.assertIn(needle,html)
 def test_09i_chat_only_bot_has_no_vm_tools(self):
  names=[t['function']['name'] for t in Model.calls[-1]['tools']]
  for vm_tool in ('run_command','fetch_room_file','share_room_file','browser_open'):self.assertNotIn(vm_tool,names)
  self.assertIn('remember',names)
  self.assertIn('KEINE eigene VM',Model.calls[-1]['messages'][0]['content'])
 def test_09j_prompt_progress_does_not_break_reply(self):
  prog={'_chunk':{'choices':[],'prompt_progress':{'total':4694,'cache':0,'processed':2048,'time_ms':100000}}}
  Model.script=[[prog,'Fertig ','gelesen.']]
  try:
   self.request('/api/message',{'text':'Fortschritt','bots':['test']})
   self.wait_room('Fertig gelesen.');self.wait_idle()
  finally:Model.script=[]
  html=self.request('/',raw=True).decode()
  for needle in ("event.type==='progress'",'Liest Verlauf','streamClock','function promptEta',"READ_LABEL='Liest das Gespräch'",'noch ca. '):self.assertIn(needle,html)
 def test_09k_thinking_toggle_per_message(self):
  self.request('/api/message',{'text':'Denk nach','bots':['test'],'think':True});self.wait_room('Hallo aus dem Modell.');self.wait_idle()
  self.assertEqual(Model.calls[-1]['chat_template_kwargs'],{'enable_thinking':True})
  self.assertTrue(self.request('/api/state')['thinking'])
  self.request('/api/message',{'text':'Schnell bitte','bots':['test'],'think':False});self.wait_room('Hallo aus dem Modell.');self.wait_idle()
  self.assertEqual(Model.calls[-1]['chat_template_kwargs'],{'enable_thinking':False})
  st=self.request('/api/state');self.assertFalse(st['thinking']);self.assertTrue(st['thinking_default'])
  # Same stable system prompt either way -> prompt cache unaffected by the toggle
  self.assertEqual(Model.calls[-1]['messages'][0]['content'],Model.calls[-2]['messages'][0]['content'])
  self.request('/api/message',{'text':'Standard','bots':['test']});self.wait_room('Hallo aus dem Modell.');self.wait_idle()
  self.assertTrue(Model.calls[-1]['chat_template_kwargs']['enable_thinking'],'Without a choice the room default applies')
  html=self.request('/',raw=True).decode()
  for needle in ('id="think"',"think:$('think').checked","mag-think",'state.thinking_default'):self.assertIn(needle,html)
 def test_09l_new_message_is_queued_not_aborting(self):
  Model.delay=1.5
  try:
   before=len(Model.calls)
   self.request('/api/message',{'text':'Erste Frage','bots':['test']})
   for _ in range(100):
    if self.request('/api/state')['busy']:break
    time.sleep(.02)
   r=self.request('/api/message',{'text':'Zweite Frage','bots':['test']})
   self.assertTrue(r.get('queued'),'Second message must be queued while a turn runs')
   r3=self.request('/api/message',{'text':'Dritte Frage','bots':['test']})
   self.assertTrue(r3.get('queued'))
   for _ in range(200):
    s=self.request('/api/state')
    if not s['busy'] and len(Model.calls)>=before+3:break
    time.sleep(.05)
   time.sleep(.3)
   self.assertEqual(len(Model.calls),before+3,"Queued messages run in order, one turn each (merge is explicit)")
   answers=[m for m in self.request('/api/state')['room'] if m.get('role')=='assistant' and m.get('content')=='Hallo aus dem Modell.']
   self.assertGreaterEqual(len(answers),2,'The running turn must finish instead of being aborted')
   last=Model.calls[-1]['messages'][1]['content']
   self.assertIn('Zweite Frage',last);self.assertIn('Dritte Frage',last)
   html=self.request('/',raw=True).decode()
   self.assertIn("event.type==='queue'",html);self.assertIn('queue-chips',html);self.assertIn('Jetzt senden',html);self.assertNotIn('Eingereiht',html)
  finally:Model.delay=0
 def test_09m_interrupt_still_aborts(self):
  Model.delay=2
  try:
   self.request('/api/message',{'text':'Lange Aufgabe','bots':['test']})
   for _ in range(100):
    if self.request('/api/state')['busy']:break
    time.sleep(.02)
   start=time.monotonic()
   r=self.request('/api/message',{'text':'Sofort','bots':['test'],'interrupt':True})
   self.assertFalse(r.get('queued'))
   self.wait_idle()
   self._settle(lambda s:not s['busy'] and s['room'][-2].get('content')=='Sofort')
   self.assertLess(time.monotonic()-start,4.5)
  finally:Model.delay=0
 def test_10_verified_tool_step_can_continue_once(self):
  command={'tool_calls':[{'index':0,'id':'call_test_1','function':{'name':'remember','arguments':'{"fact":"autonomy test","source":"test"}'}}]}
  Model.script=[[command],['Arbeit begonnen [[MAG:WEITER]]'],['Arbeit abgeschlossen [[MAG:ENDE]]']]
  try:
   self.request('/api/autotalk',{'on':True})
   initial=len(Model.calls)
   self.request('/api/message',{'text':'Merke eine Testnotiz und pruefe sie weiter','bots':['test']})
   for _ in range(160):
    state=self.request('/api/state')
    bot_messages=[m.get('content','') for m in state['room'] if m.get('role')=='assistant']
    if len(Model.calls)>=initial+3 and 'Arbeit abgeschlossen' in bot_messages:break
    time.sleep(.05)
   else:self.fail('Verified tool action did not yield one controlled continuation %d %r'%(len(Model.calls)-initial,bot_messages[-4:]))
   self.assertEqual(len(Model.calls),initial+3)
   self.assertFalse(any('[[MAG:' in x for x in bot_messages),'Do not persist internal markers')
   time.sleep(1.2)
   self.assertEqual(len(Model.calls),initial+3,'Final status must not trigger another bot turn')
   self.assertTrue(state['autotalk'],'Autonomy may remain enabled while idle')
  finally:
   self.request('/api/stop',{});self.request('/api/queue/resume',{});Model.script=[]
 def test_10b_coding_run_many_tool_steps(self):
  """A single user instruction performs many tool calls without autotalk."""
  self.wait_idle();Model.calls.clear()
  script=[]
  for i in range(18):
   script.append([{'tool_calls':[{'index':0,'id':f'call_long_{i}',
      'function':{'name':'remember','arguments':json.dumps({'fact':f'Long step {i}'})}}]}])
  Model.script=script+[['Alle 18 Schritte abgeschlossen. [[MAG:ENDE]]']]
  try:
   self.request('/api/autotalk',{'on':False})
   self.request('/api/message',{'text':'Fuehre 18 dokumentierte Schritte aus','bots':['test']})
   state=self._settle(lambda s:not s['busy'] and any(
       'Alle 18 Schritte abgeschlossen.' in m.get('content','') for m in s['room']),n=400)
   self.assertEqual(len(Model.calls),19,'do not stop at six tool rounds')
   self.assertGreaterEqual(len(state['room'][-1].get('steps') or []),1)
  finally:Model.script=[]
 def test_10c_announced_next_step_is_not_final(self):
  self.wait_idle();Model.calls.clear()
  Model.script=[[{'content':'Nächster Schritt: Ausführung und Kontrolle.'}],
    [{'tool_calls':[{'index':0,'id':'call_pending',
      'function':{'name':'remember','arguments':'{"fact":"Kontrolle ausgefuehrt"}'}}]}],
    ['Kontrolle durchgeführt. [[MAG:ENDE]]']]
  try:
   self.request('/api/message',{'text':'Pruefe mit Werkzeug statt Ankuendigung','bots':['test']})
   state=self._settle(lambda s:not s['busy'] and any(
      'Kontrolle durchgeführt.' in m.get('content','') for m in s['room']),n=200)
   self.assertEqual(len(Model.calls),3)
   self.assertNotIn('Nächster Schritt',state['room'][-1].get('content',''))
  finally:Model.script=[]
 def test_11_empty_model_response_never_leaves_a_blank_bot(self):
  Model.reply=''
  try:
   self.request('/api/message',{'text':'Antworte auch bei leerem Backend','bots':['test']})
   for _ in range(100):
    state=self.request('/api/state')
    if any('Es wurde keine Antwort erzeugt.' in m.get('content','') for m in state['room']):break
    time.sleep(.05)
   else:self.fail('Blank model response left the bot without feedback')
  finally:Model.reply=None
 def test_11_frontend_live_contract(self):
  html=self.request('/',raw=True).decode()
  self.assertIn('id="terminal"',html)
  self.assertIn('id="terminal-panel"',html)
  self.assertIn('new Terminal(',html)
  self.assertIn('terminal_input',html)
  self.assertIn('terminal_resize',html)
  self.assertIn('#terminal .terminal-host{',html)
  self.assertIn('overflow:hidden;contain:layout size',html)
  self.assertIn('overscroll-behavior:contain!important',html)
  self.assertIn("addEventListener('wheel',e=>{",html)
  self.assertIn('entry.term.scrollLines(lines)',html)
  self.assertIn('e.preventDefault()',html)
  self.assertIn('e.stopPropagation()',html)
  self.assertIn('term.scrollToBottom()',html)
  self.assertIn("activeTerminal.host.parentElement!==$('terminal')",html)
  self.assertNotIn('id="activity"',html)
  self.assertNotIn('id="console-form"',html)
  self.assertIn('id="left-panel"',html)
  self.assertIn('id="right-panel"',html)
  self.assertIn('id="screen"',html)
  self.assertIn('id="browser-agent"',html)
  self.assertIn('id="browser-url"',html)
  self.assertIn('id="browser-open"',html)
  self.assertIn('id="live"',html)
  self.assertIn("browserAction('navigate'",html)
  self.assertIn('id="browser-fullscreen"',html)
  self.assertIn('id="terminal-fullscreen"',html)
  self.assertIn('id="browser-view"',html)
  self.assertIn("togglePanelFullscreen('browser-view')",html)
  self.assertIn("togglePanelFullscreen('terminal-panel')",html)
  self.assertIn("document.addEventListener('fullscreenchange'",html)
  for obsolete in ('browser-selector','browser-click','browser-text',
                   'browser-type','browser-scroll','browser-read'):
   self.assertNotIn(obsolete,html)
  self.assertIn("content.innerHTML=safeMarkdown(text)",html);self.assertIn("event.type==='draft'",html);self.assertIn("event.type==='draft_drop'",html)
  self.assertIn("createImageBitmap",html)
  self.assertNotIn('notice();screen();',html)
  self.error(404,'/api/console/test',{'command':'whoami'}) # no HTTP terminal fallback
 def test_11_named_remote_desktop_routes_follow_agent_lifecycle(self):
  # One interface only: /bot/<slug>/ redirects to the main room (?bot=slug).
  import http.client
  for path in ('/bot/test/','/bot/test'):
   c=http.client.HTTPConnection('127.0.0.1',self.ui,timeout=5);c.request('GET',path);r=c.getresponse();r.read();c.close()
   self.assertEqual(r.status,302);self.assertEqual(r.getheader('Location'),'/?bot=test')
  html=self.request('/',raw=True).decode()
  self.assertIn('const remoteBotId=',html);self.assertIn("get('bot')",html)
  self.assertIn("remote.href='/?bot='+encodeURIComponent(a.id)",html)
  self.assertIn('id="browser-fullscreen"',html);self.assertIn('id="terminal-fullscreen"',html)
  for gone in ('id="remote-bar"','body.remote-mode',"'/bot/'"):self.assertNotIn(gone,html)
  self.assertNotIn('window.open(',html)
  for invalid in ('/bot/not-registered/','/bot/test/extra','/bot/test%2fextra','/bot/../../'):
   self.error(404,invalid)
  token=self.state.joinpath('token').read_text().strip()
  self.error(403,'/bot/test/',bridge=True,headers={'Authorization':'Bearer '+token})
 def test_11_browser_live_requires_known_bot(self):
  self.error(404,'/api/browser-live/offer?bot=not-a-bot',{'type':'offer','sdp':'sdp'})
  self.error(404,'/api/browser/not-a-bot/pointer',{'x':1,'y':1})
  html=self.request('/',raw=True).decode()
  # A stale bot identity after single-bot migration is permanent, NOT an
  # invitation to retry WebRTC against a vanished VM forever.
  self.assertIn('err.permanent=r.status===404||r.status===400',html)
  self.assertIn('if(!e.permanent)scheduleReconnect()',html)
  self.assertIn('a.id===old&&a.vm',html)
  self.assertIn('a.id===remoteBotId&&a.vm',html)
 def test_12_malformed_http_rejected(self):
  for headers in ['Content-Length: -1\r\n','Content-Length: 2\r\nContent-Length: 3\r\n','Transfer-Encoding: chunked\r\n']:
   with socket.create_connection(('127.0.0.1',self.ui),timeout=3) as s:
    s.sendall(('POST /api/message HTTP/1.1\r\nHost: localhost\r\n'+headers+'\r\n').encode());self.assertIn(b'500',s.recv(4096))
 def test_12_browser_routes_accept_vm_alias_without_touching_legacy_vms(self):
  # Run last: restart the isolated temporary fixture with a synthetic VM alias.
  agents=json.loads(self.state.joinpath('agents.json').read_text())
  agents[0]['vm']='agent1'
  self.proc.terminate();self.proc.wait(timeout=5);self.log.close()
  self.state.joinpath('agents.json').write_text(json.dumps(agents))
  self.start()
  # An authorized bot alias reaches action validation (400, not 404).
  self.error(400,'/api/browser/agent1/not-an-action',{})
  self.error(400,'/api/browser/test/not-an-action',{})
  self.error(404,'/api/browser/unknown/not-an-action',{})
  self.error(404,'/api/screen/unknown')

 def test_13_remote_route_disappears_when_bot_deleted(self):
  # Test agent has vm="" so deleting it requires no guest lifecycle.
  # Keep the original fixture intact for future test runs.
  saved=self.state.joinpath('agents.json').read_text()
  # test_12 installs a synthetic VM name to verify aliases; clear that
  # fixture before exercising DELETE so no host lifecycle is invoked.
  self.proc.terminate();self.proc.wait(timeout=5);self.log.close()
  stub=json.loads(saved);stub[0]['vm']=''
  self.state.joinpath('agents.json').write_text(json.dumps(stub))
  self.start()
  try:
   self.assertIn(b'<html',self.request('/bot/test/',raw=True))
   self.assertTrue(self.request('/api/agents/test',method='DELETE')['ok'])
   self.error(404,'/bot/test/')
  finally:
   self.proc.terminate();self.proc.wait(timeout=5);self.log.close()
   self.state.joinpath('agents.json').write_text(saved)
   self.start()
  self.assertIn(b'<html',self.request('/bot/test/',raw=True))

 def _ts(self, login, name, body=True):
  # Native LocalAPI attestation is part of the regression, not a bypass.
  addr=f'100.101.102.103:{10000+len(WHOIS_PROFILES)%50000}'
  WHOIS_PROFILES[addr]=(login,name)
  req=urllib.request.Request(f'http://127.0.0.1:{self.ui}/_internal/tailscale/whois',
                             headers={'X-Mag-Remote':addr})
  with urllib.request.urlopen(req,timeout=5) as resp:
   h={k:v for k,v in resp.headers.items()
      if k.lower().startswith(('x-tailscale-','x-mag-auth-'))}
  if body:h['Content-Type']='application/json'
  return h
 def _access(self, owners, perms=None, owner_name='Chris'):
  base={k:['owner'] for k in ('offline','stop','jobs','memory','reset','delete','autotalk','connectors','browser')}
  if perms:base.update(perms)
  self.state.joinpath('access.json').write_text(json.dumps({'owners':owners,'owner_name':owner_name,'permissions':base}))
 def test_17h_owner_model_zero_one_switch_is_persistent_and_owner_only(self):
  self.wait_idle();self._access(['chris@github'])
  owner=self._ts('chris@github','Chris')
  guest=self._ts('ada@github','Ada')
  q4='deepseek-v4-flash-0731';iq3='deepseek-v4-flash-0731-iq3'
  try:
   room=self.request('/api/state',headers=owner)['room']
   pid=self.proc.pid
   catalog=self.request('/api/model',headers=owner)
   self.assertEqual([m['id'] for m in catalog['models']],[q4,iq3])
   self.error(403,'/api/model',{'id':iq3},headers=guest)
   self.error(400,'/api/model',{'id':'unknown'},headers=owner)
   self.error(410,'/api/models',{'id':'iq3'},method='PUT',headers=owner)
   reply=self.request('/api/model',{'id':iq3},headers=owner)
   self.assertTrue(reply['ok']);self.assertFalse(reply['restart'])
   self.assertEqual(self.request('/api/state',headers=owner)['model'],iq3)
   self.assertEqual(self.request('/api/model',headers=owner)['model'],iq3)
   self.assertEqual((self.state/'room-model.txt').read_text().strip(),iq3)
   html=self.request('/',raw=True).decode()
   self.assertIn('id="model-choice"',html)
   self.assertIn('id="single-model-switch"',html)
   self.assertIn('id="single-model-select"',html)
   self.assertIn('Modell wählen · Q4 /',html)
   self.assertIn('>0 · Q4</button>',html)
   self.assertIn('>1 · IQ3</button>',html)
   self.assertIn('aria-pressed=',html)
   self.assertNotIn('id="model-pick"',html)
   self.assertEqual(self.request('/api/state',headers=owner)['room'],room)
   self.assertEqual(self.proc.pid,pid)
  finally:
   self.request('/api/model',{'id':q4},headers=owner)
   self.state.joinpath('access.json').unlink(missing_ok=True)
  self.assertEqual(self.request('/api/state')['model'],q4)
 def test_17p_no_nginx_whois_or_oauth_keeps_owner_and_local_services(self):
  # This is the outage regression: no credential directory/secret, no nginx
  # attestation headers on local host calls. Existing room must remain usable.
  self.wait_idle()
  self._access(["chris@github"])
  html=self.request('/',raw=True)
  self.assertIn(b'<html',html)
  local=self.request('/api/state')
  self.assertEqual(local['you']['role'],'owner')
  self.assertEqual(local['you']['login'],'')
  self.assertTrue(self.request('/readyz')['ready'])
  self.assertEqual(self.request('/healthz',raw=True),b'ok\n')
  # Local headless Chrome/Box operations must retain the existing loopback
  # interface, not a guest or a forged remote-header identity.
  self.assertIn('room',self.request('/api/state'))
  self.error(403,'/api/guests')
  self.error(403,'/api/room/clear',{})
  self.error(403,'/api/state',headers={'X-Tailscale-User':'chris@github'})
  owner=self._ts('chris@github','Chris')
  self.assertEqual(self.request('/api/state',headers=owner)['you']['role'],'owner')
  guests=self.request('/api/guests',headers=owner)
  self.assertFalse(guests['configured'])
  self.assertEqual(guests['live_status'],'disabled')
  self.assertIn('deaktiviert',guests['notice'])
  # Valid owner, valid CSRF, but optional OAuth absent: no key is created.
  signed={**owner,'Host':'mag.stardenburg.de',
          'Origin':'https://mag.stardenburg.de','Sec-Fetch-Site':'same-origin'}
  with self.assertRaises(urllib.error.HTTPError) as caught:
   self.request('/api/guests',{'name':'test','expires_at':int(time.time())+86400},
                headers=signed)
  self.assertEqual(caught.exception.code,503)
  err=json.loads(caught.exception.read())
  self.assertIn('OAuth',err['error'])
  self.assertNotIn('key',err)
  # A non-owner is not elevated by missing nginx/OAuth.
  visitor=self._ts('ada@github','Ada')
  self.assertEqual(self.request('/api/state',headers=visitor)['you']['role'],'guest')
  self.error(403,'/api/guests',headers=visitor)
  self.error(403,'/api/room/clear',{},headers=visitor)
  self.assertEqual(self.request('/api/state',headers=owner)['you']['role'],'owner')

 def test_17_guest_identity_roles_and_prompt_cache(self):
  self.wait_idle();self._access(['chris@github'])
  st=self.request('/api/state',headers={'X-Forwarded-For':'8.8.8.8'})
  self.assertEqual(st['you']['role'],'owner');self.assertEqual(st['you']['login'],'');self.assertEqual(st['you']['name'],'Chris')
  st=self.request('/api/state',headers=self._ts('ada@github','Ada Lovelace',False))
  self.assertEqual(st['you']['role'],'guest');self.assertEqual(st['you']['name'],'Ada Lovelace');self.assertEqual(st['you']['login'],'ada@github')
  for key in ('offline','stop','jobs','memory','reset','delete'):self.assertFalse(st['you']['permissions'][key])
  st=self.request('/api/state',headers=self._ts('Chris@GitHub','Chris',False))
  self.assertEqual(st['you']['role'],'owner');self.assertEqual(st['you']['login'],'chris@github')
  self.error(403,'/api/state',headers={'X-Tailscale-User':'not a login'})
  h=self._ts('ada@github','Ada')
  for path,body,method in (('/api/offline',{'on':True},'POST'),('/api/stop',{},'POST'),('/api/reset',{},'POST'),('/api/autotalk',{'on':True},'POST'),('/api/jobs',{'name':'x','prompt':'y','schedule':{'type':'none'}},'POST'),('/api/agents/test/memory/0',{'text':'nein','old':'nope'},'PUT'),('/api/browser/test/read',{},'POST')):
   self.error(403,path,body,method=method,headers=h)
  self.error(403,'/api/agents/test',method='DELETE',headers=self._ts('ada@github','Ada',False))
  self.error(403,'/api/jobs',method='GET',headers=self._ts('ada@github','Ada',False))
  self.assertFalse(self.request('/api/state')['offline'],'denied guest offline must not flip the switch')
  Model.calls.clear();Model.script=[['Hallo Ada.'],['Hallo Bob.']]
  try:
   self.request('/api/message',{'text':'ich bin ada','bots':['test']},headers=self._ts('ada@github','Ada Lovelace'))
   self.wait_room('Hallo Ada.');self.wait_idle()
   user=[m for m in self.request('/api/state')['room'] if m.get('content')=='ich bin ada'][-1]
   self.assertEqual(user['name'],'Ada Lovelace');self.assertEqual(user['login'],'ada@github');self.assertEqual(user['role'],'user')
   call=Model.calls[-1]
   self.assertNotIn('Ada',call['messages'][0]['content'])
   self.assertNotIn('Ada',json.dumps(call.get('tools')))
   self.assertRegex(call['messages'][1]['content'],r'\[Ada Lovelace \([0-9a-f]+\)\]: ich bin ada')
   sys1=call['messages'][0];tools=json.dumps(call.get('tools'))
   self.request('/api/message',{'text':'ich bin bob','bots':['test']},headers=self._ts('bob@github','Bob'))
   self.wait_room('Hallo Bob.');self.wait_idle()
   call2=Model.calls[-1]
   self.assertEqual(call2['messages'][0],sys1,'sender must not touch the system prompt')
   self.assertEqual(json.dumps(call2.get('tools')),tools)
   self.assertRegex(call2['messages'][1]['content'],r'\[Bob \([0-9a-f]+\)\]: ich bin bob')
   self.assertNotIn('Bob',call2['messages'][0]['content'])
  finally:
   Model.script=[]
  self._access(['chris@github'],{'offline':['owner','guest']})
  self.request('/api/offline',{'on':True},headers=h);self.assertTrue(self.request('/api/state')['offline'])
  self.request('/api/offline',{'on':False},headers=h);self.assertFalse(self.request('/api/state')['offline'])
  self._access(['chris@github'])
  self.error(403,'/api/offline',{'on':True},headers=h)
  Model.script=[self._slow('out',25)]
  try:
   self.request('/api/message',{'text':'owner-hold','bots':['test']});self._busy()
   r=self.request('/api/message',{'text':'gast-wartet','bots':['test']},headers=self._ts('ada@github','Ada'))
   self.assertTrue(r['queued']);qid=r['item']['id'];self.assertEqual(r['item']['sender'],'Ada');self.assertEqual(r['item']['login'],'ada@github')
   self.request('/api/queue/'+qid,{'text':'gast-neu'},method='PUT',headers=self._ts('ada@github','Ada'))
   self.error(403,'/api/queue/'+qid,method='DELETE',headers=self._ts('bob@github','Bob',False))
   self.error(403,'/api/queue/'+qid+'/now',{},headers=self._ts('ada@github','Ada'))
   self.request('/api/queue/'+qid,method='DELETE',headers=self._ts('ada@github','Ada',False))
   self.assertFalse(any(p['id']==qid for p in self.request('/api/state')['pending']))
  finally:
   Model.script=[];self.request('/api/stop',{});self.wait_idle();self.request('/api/queue/resume',{});self.wait_idle()
  html=self.request('/',raw=True).decode()
  for needle in ('id="you-badge"','function isMine','function applyAccess','message.user.other','avatarColor'):
   self.assertIn(needle,html)
  for bad in ('alert(','confirm(','prompt('):self.assertNotIn(bad,html)
  # C++ local Auth endpoint, never a Python daemon; fail closed.
  self.error(403,'/_internal/tailscale/whois',headers={'X-Mag-Remote':'bad-address'})
  self.assertFalse((ROOT/'ops/private-access/tailscale-whois.py').exists())
  self.assertFalse((ROOT/'ops/private-access/mag-whois.service').exists())
  self.assertFalse((ROOT/'ops/private-access/Caddyfile').exists())
  nginx=(ROOT/'ops/private-access/mag-room.nginx.conf').read_text()
  for needle in ('listen 100.123.114.16:443','auth_request /_mag_whois',
                 'proxy_pass http://127.0.0.1:8766/_internal/tailscale/whois;',
                 'proxy_set_header X-Tailscale-User ""',
                 'proxy_set_header X-Tailscale-Name ""',
                 'proxy_set_header X-Tailscale-Login ""',
                 'proxy_set_header X-Tailscale-User $mag_user',
                 'proxy_set_header X-Mag-Remote "$remote_addr:$remote_port"',
                 'proxy_pass http://127.0.0.1:8766',
                 'proxy_buffering off','"no-store"'):
   self.assertIn(needle,nginx)
  policy=(ROOT/'ops/tailscale/policy.hujson').read_text()
  self.assertIn('100.123.114.16',policy);self.assertIn('tcp:443',policy);self.assertIn('group:mag-guests',policy)
  self.assertNotIn('tcp:22',policy);self.assertNotIn('"*"',policy)
  self.state.joinpath('access.json').unlink(missing_ok=True)
 def test_17b_non_loopback_identity_headers_are_denied(self):
  ui,bridge=port(),port();tmp=pathlib.Path(tempfile.mkdtemp(prefix='magspoof-'));state=tmp/'state';state.mkdir()
  state.joinpath('agents.json').write_text(json.dumps([{'id':'test','name':'Test','vm':'','memory':[]}]))
  state.joinpath('access.json').write_text(json.dumps({'owners':['chris@github'],'owner_name':'Chris'}))
  env=dict(os.environ,MAG_OWNER_CANONICAL_LOGIN='chris@github',MAG_TEST_ALLOW_LOCAL_OWNER='1',MAG_ROOM_STATE=str(state),MAG_ROOM_PORT=str(ui),MAG_ROOM_BRIDGE_PORT=str(bridge),MAG_ROOM_BRIDGE_IP='127.0.0.1',MAG_ROOM_BIND='0.0.0.0',MAG_ROOM_BACKEND=f'http://127.0.0.1:{self.model.server_port}/v1/chat/completions',MAG_ROOM_UI=str(ROOT/'mag-room/web/index.html'),MAG_ROOM_STATIC=str(ROOT/'mag-room/static'),MAG_NETMODE=str(NETMODE),MAG_TEST_NO_MEMGIT='1',MAG_ROOM_MODEL_STATUS=str(tmp/'ms.json'),MAG_ROOM_MCP_AUTO='0')
  log=open(tmp/'log','w');proc=subprocess.Popen([str(ROOT/'build/magroom')],env=env,stdout=log,stderr=log)
  try:
   for _ in range(100):
    try:urllib.request.urlopen(f'http://127.0.0.1:{ui}/healthz',timeout=2);break
    except Exception:time.sleep(.03)
   else:log.flush();self.fail('spoof server: '+tmp.joinpath('log').read_text()[-2000:])
   # Native route must now reject identity-header forgery even from localhost.
   forged=urllib.request.Request(f'http://127.0.0.1:{ui}/api/state',headers={'X-Tailscale-User':'chris@github','X-Tailscale-Name':'Chris'})
   with self.assertRaises(urllib.error.HTTPError) as error:
    urllib.request.urlopen(forged,timeout=5)
   self.assertEqual(error.exception.code,403)
   plain=json.loads(urllib.request.urlopen(f'http://127.0.0.1:{ui}/api/state',timeout=5).read())
   self.assertEqual(plain['you']['role'],'owner');self.assertEqual(plain['you']['login'],'')
   # Discover an IPv4 address owned by THIS host, not fixed Docker/CI IPs.
   # UDP connect chooses a local route but sends no packets.
   import ipaddress
   candidates=set()
   for dest in ('192.0.2.1','198.51.100.1'):
    try:
     with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as probe:
      probe.connect((dest,9));candidates.add(probe.getsockname()[0])
    except OSError:pass
   try:
    for item in socket.getaddrinfo(socket.gethostname(),None,socket.AF_INET):
     candidates.add(item[4][0])
   except OSError:pass
   # Linux SIOCGIFADDR additionally handles hosts whose DNS has no local A.
   if os.name=='posix':
    try:
     import fcntl,struct
     for _,name in socket.if_nameindex():
      with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as probe:
       try:
        raw=fcntl.ioctl(probe.fileno(),0x8915,struct.pack('256s',name[:15].encode()))
        candidates.add(socket.inet_ntoa(raw[20:24]))
       except OSError:pass
    except (ImportError,OSError):pass
   host=''
   for cand in sorted(candidates):
    try:
     ip=ipaddress.ip_address(cand)
     if ip.is_loopback or ip.is_unspecified or ip.is_multicast or ip.is_link_local:continue
     with socket.create_connection((cand,ui),timeout=.5):host=cand;break
    except (OSError,ValueError):continue
   if not host:self.skipTest('No reachable non-loopback interface on this host')
   # Bypass CI HTTP_PROXY: this must hit the real locally bound test server.
   direct=urllib.request.build_opener(urllib.request.ProxyHandler({}))
   req=urllib.request.Request(f'http://{host}:{ui}/api/state',headers={'X-Tailscale-User':'chris@github','X-Tailscale-Name':'Chris'})
   with self.assertRaises(urllib.error.HTTPError) as ctx:direct.open(req,timeout=5)
   self.assertEqual(ctx.exception.code,403);self.assertIn('Kein Zugang',ctx.exception.read().decode())
   req=urllib.request.Request(f'http://{host}:{ui}/healthz')
   with self.assertRaises(urllib.error.HTTPError) as ctx:direct.open(req,timeout=5)
   self.assertEqual(ctx.exception.code,403)
  finally:
   proc.terminate();proc.wait(timeout=5);log.close()

 def test_16_interim_narration_is_a_step_not_the_answer(self):
  first=[{'content':'Ich prüfe die Webseite.\n'},{'tool_calls':[{'index':0,'id':'call_step','function':{'name':'remember','arguments':'{"fact":"aus dem schritt"}'}}]}]
  Model.script=[first,['Die Seite ist gelesen.']]
  try:
   self.request('/api/message',{'text':'Schau auf die Seite','bots':['test']})
   self.wait_room('Die Seite ist gelesen.');self.wait_idle()
  finally:
   Model.script=[]
  ans=[m for m in self.request('/api/state')['room'] if m.get('role')=='assistant' and 'Seite ist gelesen' in (m.get('content') or '')]
  self.assertTrue(ans)
  self.assertNotIn('Ich prüfe die Webseite', ans[-1]['content'])
  steps=ans[-1].get('steps') or []
  self.assertTrue(any('Webseite' in s for s in steps), steps)
  self.assertTrue(any('Merkt sich' in s for s in steps), steps)
  self.assertGreaterEqual(ans[-1].get('elapsed_s', -1), 0)
  self.assertNotIn('[[MAG:', ans[-1]['content'])
  html=self.request('/',raw=True).decode()
  for needle in ('work-status','Keine Rückmeldung seit ','Fertig ·',"event.type==='step'",'phase-hung','phase-error','Liest Verlauf'):
   self.assertIn(needle, html)
 def test_16a_answer_streams_as_draft_narration_is_revoked(self):
  self.wait_idle();events=[]
  def listen():
   r=urllib.request.urlopen(f'http://127.0.0.1:{self.ui}/api/events',timeout=10);r.fp.raw._sock.settimeout(6)
   try:
    while True:
     line=r.readline().decode()
     if line.startswith('data: '):
      e=json.loads(line[6:]);events.append(e)
      if e.get('type')=='bot_done':break
   except Exception:pass
   finally:r.close()
  t=threading.Thread(target=listen,daemon=True);t.start();time.sleep(.3)
  first=[{'content':'Ich schaue kurz nach.\n'},{'tool_calls':[{'index':0,'id':'call_d','function':{'name':'remember','arguments':'{"fact":"entwurf"}'}}]}]
  Model.script=[first,['Erste Zeile.\n','Zweite Zeile.']]
  try:
   self.request('/api/message',{'text':'Entwurf bitte','bots':['test']})
   self.wait_room('Zweite Zeile.');self.wait_idle()
  finally:Model.script=[]
  t.join(8)
  kinds=[e.get('type') for e in events]
  drafts=[e.get('text') for e in events if e.get('type')=='draft']
  self.assertIn('Ich schaue kurz nach.\n',drafts,kinds)
  self.assertIn('draft_drop',kinds)
  self.assertLess(kinds.index('draft_drop'),kinds.index('tool') if 'tool' in kinds else len(kinds))
  last_drop=len(kinds)-1-kinds[::-1].index('draft_drop')
  self.assertEqual(''.join(e.get('text','') for e in events[last_drop:] if e.get('type')=='draft'),'Erste Zeile.\nZweite Zeile.')
  self.assertLess(kinds.index('draft',last_drop),kinds.index('delta',last_drop),'the answer is visible before the round ends')
  ans=[m for m in self.request('/api/state')['room'] if m.get('role')=='assistant' and 'Zweite Zeile' in (m.get('content') or '')][-1]
  self.assertFalse(any('Zeile' in s for s in ans.get('steps') or []),ans.get('steps'))
  self.assertTrue(any('schaue kurz nach' in s for s in ans.get('steps') or []),ans.get('steps'))
 def test_16i_tool_calls_pair_input_output_and_survive_reload(self):
  self.wait_idle();events=[]
  def listen():
   r=urllib.request.urlopen(f'http://127.0.0.1:{self.ui}/api/events',timeout=10);r.fp.raw._sock.settimeout(6)
   try:
    while True:
     line=r.readline().decode()
     if line.startswith('data: '):
      e=json.loads(line[6:]);events.append(e)
      if e.get('type')=='bot_done':break
   except Exception:pass
   finally:r.close()
  t=threading.Thread(target=listen,daemon=True);t.start();time.sleep(.3)
  Model.script=[[{'tool_calls':[{'index':0,'id':'call_io','function':{'name':'remember','arguments':'{"fact":"Lieblingsfarbe Blau"}'}}]}],['Gemerkt.']]
  try:
   self.request('/api/message',{'text':'Merk dir Blau','bots':['test']})
   self.wait_room('Gemerkt.');self.wait_idle()
  finally:Model.script=[]
  t.join(8)
  steps=[e for e in events if e.get('type')=='step']
  self.assertEqual((steps[0]['phase'],steps[0]['label']),('read','Liest Verlauf'),'no bare "Denkt nach" before the first token')
  call=[e for e in events if e.get('type')=='tool'][0];res=[e for e in events if e.get('type')=='tool_result'][0]
  self.assertEqual((call['id'],res['id']),('call_io','call_io'))
  self.assertEqual(call['args'],{'fact':'Lieblingsfarbe Blau'})
  self.assertTrue(res['ok']);self.assertGreaterEqual(res['ms'],0);self.assertTrue(res['result'])
  self.assertTrue(any(e['phase']=='read' and e['label']=='Liest Ergebnis' for e in steps))
  ans=[m for m in self.request('/api/state')['room'] if m.get('role')=='assistant' and 'Gemerkt.' in (m.get('content') or '')][-1]
  self.assertEqual([(x['tool'],x['args'],x['ok']) for x in ans['tools']],[('remember',{'fact':'Lieblingsfarbe Blau'},True)])
  self.assertTrue(ans['tools'][0]['result'])
  self.assertFalse(any('Liest' in s for s in ans.get('steps') or []),ans.get('steps'))
  html=self.request('/',raw=True).decode()
  for needle in ('function paintTools(','function storedTools(','msg.tools?.length','Eingabe','Ergebnis',"phase:'read'"):self.assertIn(needle,html)
 def test_17_scoped_room_memory_and_workspace(self):
  self.wait_idle()
  self._access(["chris@github"])
  owner=self._ts("chris@github","Chris")
  self.request('/api/agent/test/remember',{'fact':'trennung-bleibt'})
  self.request('/api/agent/test/say',{'text':'Trennungsnachricht'})
  room=self.request('/api/state')['room']
  mid=[m['id'] for m in room if m.get('content')=='Trennungsnachricht'][-1]
  uploaded=self.request('/api/upload?name=trennung.txt',b'trennung')
  stored=uploaded['stored']
  self.error(400,'/api/room/messages/nicht-hex',method='DELETE')
  self.error(400,'/api/room/files/..%2Fetc%2Fpasswd',method='DELETE',headers=owner)
  self.error(404,'/api/undo/abcdef12',{})
  self.error(403,'/api/room/clear',{},headers={'Content-Type':'application/json','X-No-Token':'1'})
  gone=self.request('/api/room/messages/'+mid,method='DELETE')
  self.assertNotIn(mid,[m['id'] for m in self.request('/api/state')['room']])
  self.request('/api/undo/'+gone['undo'],{})
  self.assertIn(mid,[m['id'] for m in self.request('/api/state')['room']])
  removed=self.request('/api/room/files/'+stored,method='DELETE',headers=owner)
  self.assertFalse(any(f.get('stored')==stored for f in self.request('/api/state')['files']))
  self.error(404,'/api/file/'+stored)
  self.request('/api/undo/'+removed['undo'],{})
  self.assertEqual(self.request('/api/file/'+stored,raw=True),b'trennung')
  self.assertTrue(any(x.get('text')=='trennung-bleibt' for x in self.request('/api/agents/test/memory')))
  cleared=self.request('/api/agents/test/memory',method='DELETE')
  self.assertEqual(self.request('/api/agents/test/memory'),[])
  self.request('/api/undo/'+cleared['undo'],{})
  self.assertTrue(any(x.get('text')=='trennung-bleibt' for x in self.request('/api/agents/test/memory')))
  mem=self.request('/api/agents/test/memory')
  wiped=self.request('/api/room/clear',{},headers=owner)
  self.assertNotIn('backup_queued',wiped,'CI must not schedule any real restic snapshots')
  self.assertTrue((self.state/'snapshots'/wiped['snapshot']/'room.json').is_file())
  st=self.request('/api/state')
  self.assertFalse(st['room']);self.assertFalse(st['files'])
  self.assertEqual(self.request('/api/agents/test/memory'),mem)
  self.assertFalse((self.state/'files'/stored).exists())
  self.request('/api/undo/'+wiped['undo'],{})
  st=self.request('/api/state')
  self.assertTrue(any(m.get('id')==mid for m in st['room']))
  self.assertTrue(any(f.get('stored')==stored for f in st['files']))
  self.assertEqual(self.request('/api/file/'+stored,raw=True),b'trennung')
  self.error(404,'/api/agents/kein-bot/workspace/reset',{})
  html=self.request('/',raw=True).decode()
  for needle in ('id="purge-dialog"','id="data-areas"','id="area-room"','id="area-work"','id="memory-dialog"','id="memory-title"','id="memory-clear"','/api/room/clear','workspace/reset','Rückgängig','id="reset"','id="jump-bottom"','stickToBottom','AT_BOTTOM_PX','overflow-anchor:none','ResizeObserver','aria-label="Nach unten scrollen"'):
   self.assertIn(needle, html)
  self.assertNotIn('confirm(', html)
  # The sidebar must not duplicate the existing bot memories dialog.
  for removed in ('id="area-memory"','id="memory-areas"','loadMemories',
                  '.data-entry','.data-x','mem.replaceChildren()'):
   self.assertNotIn(removed,html)
  for kept in ("if(can('memory'))actList.push(['Erinnerung',()=>memory(a)])",
               'function renderMemory(m)',"method='PUT'",'askDeleteMemory(memAgent,i,text)',
               'askClearMemory(memAgent', "id=\"work-areas\""):
   if kept=="method='PUT'":continue  # API verbs are validated by earlier tests.
   self.assertIn(kept,html)
  # Earlier tests may leave a synthetic VM alias. Clear it before /api/reset,
  # which still cleans guest rooms, and before the chat-only workspace check.
  agents=json.loads(self.state.joinpath('agents.json').read_text())
  for a in agents:a['vm']=''
  self.proc.terminate();self.proc.wait(timeout=5);self.log.close()
  self.state.joinpath('agents.json').write_text(json.dumps(agents));self.start()
  self.request('/api/agent/test/say',{'text':'Undo faellt weg'})
  mid2=[m['id'] for m in self.request('/api/state')['room'] if m.get('content')=='Undo faellt weg'][-1]
  dropped=self.request('/api/room/messages/'+mid2,method='DELETE')
  self.request('/api/reset',{})
  self.error(404,'/api/undo/'+dropped['undo'],{})
  self.assertFalse(list(self.state.joinpath('files').iterdir()))
  with self.assertRaises(urllib.error.HTTPError) as ctx:
   self.request('/api/agents/test/workspace/reset',{})
  self.assertEqual(ctx.exception.code,400)
  self.assertIn('Nur-Chat-Bot',json.loads(ctx.exception.read())['error'])
 def test_17z_room_clear_snapshot_restore_and_guest_denial(self):
  self.wait_idle()
  self._access(['chris@github'])
  owner=self._ts('chris@github','Chris')
  guest=self._ts('ada@github','Ada')
  self.request('/api/agent/test/say',{'text':'Snapshot dauerhaft'})
  file=self.request('/api/upload?name=recovery.txt',b'RECOVER ME')
  stored=file['stored']
  before=self.request('/api/state')
  room_disk=(self.state/'room.json').read_bytes()
  files_disk=(self.state/'files.json').read_bytes()
  forbidden=[
   ('/api/room/clear','POST',{}),
   ('/api/reset','POST',{}),
   ('/api/room/messages/abcdef12','DELETE',None),
   ('/api/room/files/'+stored,'DELETE',None),
   ('/api/room/snapshots','GET',None),
   ('/api/room/snapshots/1234567890-'+'a'*24+'/restore','POST',{}),
   ('/api/undo/abcdef12','POST',{}),
   ('/api/queue','DELETE',None),
   ('/api/queue/merge','POST',{}),
   ('/api/frontend','POST',{'html':'bad'}),
   ('/api/pages','POST',{}),
   ('/api/connectors/github','DELETE',None),
   ('/api/jobs','GET',None),
   ('/api/unknown-destructive','POST',{}),
  ]
  for uri,method,data in forbidden:
   with self.subTest(uri=uri,method=method):
    self.error(403,uri,data,method=method,headers=guest)
  self.assertEqual((self.state/'room.json').read_bytes(),room_disk)
  self.assertEqual((self.state/'files.json').read_bytes(),files_disk)
  self.assertEqual(self.request('/api/file/'+stored,raw=True),b'RECOVER ME')
  self.error(403,'/api/room/clear',{},headers={'X-Tailscale-User':'chris@github',
               'X-Tailscale-IP':'100.101.102.103'})
  self.error(403,'/api/room/clear',{}) # local loopback is NOT attested owner
  cleared=self.request('/api/room/clear',{},headers=owner)
  id=cleared['snapshot']
  folder=self.state/'snapshots'/id
  self.assertEqual(json.loads((folder/'room.json').read_text()),before['room'])
  self.assertEqual((folder/'files'/stored).read_bytes(),b'RECOVER ME')
  self.assertFalse(self.request('/api/state')['room'])
  self.assertFalse(self.request('/api/state')['files'])
  self.proc.terminate();self.proc.wait(timeout=5);self.log.close();self.start()
  owner=self._ts('chris@github','Chris')
  self.assertIn(id,[x['id'] for x in self.request('/api/room/snapshots',headers=owner)['snapshots']])
  self.error(403,'/api/room/snapshots/'+id+'/restore',{},headers=owner)
  h={**owner,'Host':'mag.stardenburg.de','Origin':'https://mag.stardenburg.de','Sec-Fetch-Site':'same-origin'}
  restored=self.request('/api/room/snapshots/'+id+'/restore',{},headers=h)
  self.assertEqual(restored['restored'],id)
  self.assertTrue((self.state/'snapshots'/restored['previous_snapshot']).is_dir())
  self.assertEqual(self.request('/api/file/'+stored,raw=True),b'RECOVER ME')
  st=self.request('/api/state')
  self.assertTrue(any(m.get('content')=='Snapshot dauerhaft' for m in st['room']))
  self.assertTrue(any(f.get('stored')==stored for f in st['files']))
  # A compromised/symlinked shared file must fail the snapshot, NOT clear.
  target=self.state/'files'/stored
  target.rename(target.with_suffix('.saved'))
  target.symlink_to('/etc/passwd')
  try:
   original=self.request('/api/state')['room']
   self.error(507,'/api/room/clear',{},headers=owner)
   self.assertEqual(self.request('/api/state')['room'],original)
  finally:
   target.unlink()
   target.with_suffix('.saved').rename(target)
  useful=self.request('/api/room/clear',{},headers=owner)['snapshot']
  snapshots_before=self.request('/api/room/snapshots',headers=owner)['snapshots']
  for _ in range(6):
   self.assertTrue(self.request('/api/room/clear',{},headers=owner)['unchanged'])
  self.assertEqual(self.request('/api/room/snapshots',headers=owner)['snapshots'],snapshots_before)
  self.assertTrue((self.state/'snapshots'/useful/'room.json').is_file())
  remaining=self.request('/api/room/snapshots',headers=owner)['snapshots']
  self.assertLessEqual(len(remaining),5)
  self.assertTrue(all((self.state/'snapshots'/x['id']/'meta.json').is_file() for x in remaining))

 def test_18_pwa_shell_is_not_cached(self):
  import base64
  def headers(path):
   req=urllib.request.Request('http://127.0.0.1:%s%s'%(self.ui,path))
   with urllib.request.urlopen(req,timeout=10) as r:
    return dict(r.headers), r.read()
  version=self.request('/api/version')['version']
  self.assertTrue(version)
  html_h, html=headers('/')
  text=html.decode()
  self.assertIn('no-store', html_h['Cache-Control']); self.assertNotIn('immutable', html_h['Cache-Control'])
  self.assertIn('name="mag-version" content="%s"'%version, text)
  self.assertIn('/static/marked.min.js?v=%s'%version, text)
  for needle in ('rel="manifest"','beforeinstallprompt','Neue Version – aktualisieren',"serviceWorker.register('/sw.js')",'id="pwa-install"','id="pwa-push"','Zum Home-Bildschirm','Mitteilungen einschalten','Mitteilungen sind blockiert.','id="update-toast"'):
   self.assertIn(needle, text)
  for bad in ('confirm(', 'alert(', 'prompt('):
   self.assertNotIn(bad, text)
  sw_h, sw=headers('/sw.js')
  self.assertIn('no-store', sw_h['Cache-Control']); self.assertNotIn('%%MAG_VERSION%%', sw.decode()); self.assertIn(version, sw.decode())
  man_h, man=headers('/manifest.webmanifest')
  self.assertEqual(man_h['Content-Type'].split(';')[0], 'application/manifest+json')
  self.assertIn('no-store', man_h['Cache-Control'])
  manifest=json.loads(man)
  self.assertEqual(manifest['name'], 'MAG'); self.assertEqual(manifest['short_name'], 'MAG')
  self.assertEqual(manifest['display'], 'standalone')
  self.assertEqual(manifest['theme_color'], '#050505'); self.assertEqual(manifest['background_color'], '#050505')
  self.assertEqual({i['purpose'] for i in manifest['icons']}, {'any','maskable'})
  st_h, body=headers('/static/marked.min.js?v='+urllib.request.quote(version))
  self.assertIn('immutable', st_h['Cache-Control']); self.assertNotIn('no-store', st_h['Cache-Control']); self.assertIn(b'function', body)
  plain_h, _=headers('/static/marked.min.js')
  self.assertIn('no-store', plain_h['Cache-Control']); self.assertNotIn('immutable', plain_h['Cache-Control'])
  api_h, _=headers('/api/state')
  self.assertIn('no-store', api_h['Cache-Control'])
  vapid=self.request('/api/push/vapid')
  self.assertFalse(vapid['configured']); self.assertEqual(vapid['publicKey'], '')
  pub=base64.urlsafe_b64encode(b'\x04'+b'\x11'*64).rstrip(b'=').decode()
  auth=base64.urlsafe_b64encode(b'\x22'*16).rstrip(b'=').decode()
  with self.assertRaises(urllib.error.HTTPError) as ctx:
   self.request('/api/push/subscribe', {'endpoint':'https://push.example/p/1','keys':{'p256dh':pub,'auth':auth}})
  self.assertEqual(ctx.exception.code, 503)
  self.assertIn('nicht eingerichtet', json.loads(ctx.exception.read().decode())['error'])
  icon_h, icon=headers('/static/icon-512.png?v='+urllib.request.quote(version))
  self.assertTrue(icon.startswith(b'\x89PNG')); self.assertIn('immutable', icon_h['Cache-Control'])
 def test_19_reactions_toggle_context_and_bot_turns(self):
  self.wait_idle();Model.script=[];Model.reply=None
  before=len(self.request('/api/state')['room'])
  self.request('/api/message',{'text':'Danke fuer die Hilfe','bots':['test']})
  for _ in range(120):
   room=self.request('/api/state')['room']
   if any(m.get('content')=='Danke fuer die Hilfe' for m in room[before:]) and any(m.get('role')=='assistant' and m.get('content')=='Hallo aus dem Modell.' for m in room[before:]):
    break
   time.sleep(.05)
  else:self.fail('seed turn missing')
  self.wait_idle()
  room=self.request('/api/state')['room']
  user=next(m for m in room if m.get('content')=='Danke fuer die Hilfe')
  bot=next(m for m in reversed(room) if m.get('role')=='assistant' and m.get('bot')=='test' and m.get('content')=='Hallo aus dem Modell.')
  path='/api/messages/'+bot['id']+'/reactions'
  self.error(403,path,{'emoji':'👍'},headers={'Content-Type':'application/json','X-No-Token':'1'})
  token=self.state.joinpath('token').read_text().strip()
  self.error(403,path,{'emoji':'👍'},bridge=True,headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
  self.error(400,path,{'emoji':'lol'})
  self.error(404,'/api/messages/abcd/reactions',{'emoji':'👍'})
  calls=len(Model.calls)
  added=self.request(path,{'emoji':'👍'})
  self.assertTrue(added['ok']);self.assertTrue(added['on'])
  self.assertEqual(added['reactions'],[{'emoji':'👍','actor':'user','name':'Nutzer'}])
  time.sleep(.35)
  self.assertEqual(len(Model.calls),calls,'a reaction must not start a model run')
  off=self.request(path,{'emoji':'👍'})
  self.assertFalse(off['on']);self.assertEqual(off['reactions'],[])
  on=self.request(path,{'emoji':'👍'})
  self.assertTrue(on['on'])
  disk=json.loads(self.state.joinpath('room.json').read_text())
  stored=next(m for m in disk if m['id']==bot['id'])
  self.assertEqual(stored['reactions'],[{'emoji':'👍','actor':'user'}])
  notes=json.loads(self.state.joinpath('reaction_notices.json').read_text())
  self.assertTrue(any(n['message_id']==bot['id'] and n['emoji']=='👍' and n['bot']=='test' for n in notes))
  self.assertNotIn('👍',self.state.joinpath('agents.json').read_text())
  before_calls=len(Model.calls)
  self.request('/api/message',{'text':'Und noch etwas','bots':['test']})
  for _ in range(120):
   if len(Model.calls)>before_calls and 'Und noch etwas' in Model.calls[-1]['messages'][1]['content']:
    first_call=Model.calls[-1];break
   time.sleep(.05)
  else:self.fail('Reaction-aware follow-up did not reach the fake model')
  self.wait_idle()
  ctx=first_call['messages'][1]['content']
  self.assertIn('[Reaktion] Nutzer: 👍 auf deine Nachricht "Hallo aus dem Modell."',ctx)
  names=[t['function']['name'] for t in first_call['tools']]
  self.assertIn('react',names);self.assertIn('unreact',names)
  self.assertIn('Reagiere statt zu antworten',first_call['messages'][0]['content'])
  before_calls=len(Model.calls)
  self.request('/api/message',{'text':'Ohne neue Reaktion','bots':['test']})
  for _ in range(120):
   if len(Model.calls)>before_calls and 'Ohne neue Reaktion' in Model.calls[-1]['messages'][1]['content']:
    second_call=Model.calls[-1];break
   time.sleep(.05)
  else:self.fail('Second turn did not reach the fake model')
  self.wait_idle()
  self.assertNotIn('[Reaktion]',second_call['messages'][1]['content'])
  uid=user['id']
  Model.reply=''
  Model.script=[[{'tool_calls':[{'index':0,'id':'call_react','function':{'name':'react','arguments':json.dumps({'message_id':uid,'emoji':'🎉'})}}]}]]
  try:
   self.request('/api/message',{'text':'Kurzes Danke','bots':['test']})
   st=self._settle(lambda s:any(
    m.get('id')==uid and any(r['actor']=='test' and r['emoji']=='🎉'
                               for r in m.get('reactions') or [])
    for m in s['room']))
   self.wait_idle()
   target=next(m for m in st['room'] if m['id']==uid)
   self.assertTrue(any(r['actor']=='test' and r['emoji']=='🎉' for r in target.get('reactions') or []))
   ack=next(m for m in reversed(st['room']) if m.get('role')=='assistant' and m.get('bot')=='test' and m.get('content')=='')
   self.assertIsInstance(ack.get('elapsed_s'),(int,float))
   self.assertNotIn('work_error',ack);self.assertNotIn('interrupted',ack)
   self.assertTrue(any('Reagiert' in s for s in (ack.get('steps') or [])))
   self.assertNotIn('keine Antwort erzeugt',ack.get('content') or '')
  finally:
   Model.reply=None;Model.script=[]
  Model.script=[[{'tool_calls':[{'index':0,'id':'call_both','function':{'name':'react','arguments':json.dumps({'message_id':uid,'emoji':'👀'})}}]}],['Beides passt.']]
  try:
   self.request('/api/message',{'text':'Bitte beides','bots':['test']})
   self.wait_room('Beides passt.');self.wait_idle()
  finally:
   Model.script=[]
  st=self.request('/api/state')
  target=next(m for m in st['room'] if m['id']==uid)
  self.assertTrue(any(r['emoji']=='👀' and r['actor']=='test' for r in target.get('reactions') or []))
  self.assertTrue(any(m.get('content')=='Beides passt.' for m in st['room']))
  self.request('/api/room/messages/'+uid,method='DELETE')
  st=self.request('/api/state')
  self.assertFalse(any(m.get('id')==uid for m in st['room']))
  disk=json.loads(self.state.joinpath('room.json').read_text())
  self.assertFalse(any(m.get('id')==uid for m in disk))
  note_path=self.state.joinpath('reaction_notices.json')
  notes=json.loads(note_path.read_text()) if note_path.exists() else []
  self.assertFalse(any(n.get('message_id')==uid for n in notes))
  html=self.request('/',raw=True).decode()
  for needle in ('react-bar','react-pill',"event.type==='reaction'",'/api/messages/'):
   self.assertIn(needle,html)
  for bad in ('confirm(','alert(','prompt('):
   self.assertNotIn(bad,html)

 def test_11z_main_browser_focus_navigation_contract(self):
  html=self.request('/',raw=True).decode()
  for needle in ("function focusBot(", "card.onclick=e=>", "card.onkeydown=e=>",
                 "card.classList.toggle('active'", "sessionStorage.setItem('mag-focused-bot'",
                 "browser('state',{})", "id=\"browser-access\"", "can_go_back",
                 "can_go_forward"):
   self.assertIn(needle,html)

 # ---------------- native Jobs MCP: one service, identical UI representation
 def _mcp(self,method,params=None,token=True):
  req={'jsonrpc':'2.0','id':17,'method':method}
  if params is not None:req['params']=params
  headers={'Content-Type':'application/json','Accept':'application/json, text/event-stream'}
  if params and params.get('_meta',{}).get('io.modelcontextprotocol/protocolVersion')=='2026-07-28':
   headers['MCP-Protocol-Version']='2026-07-28'
   headers['Mcp-Method']=method
   if method=='tools/call':headers['Mcp-Name']=params['name']
  if token:headers['Authorization']='Bearer local-unit-test-mcp-token-0123456789'
  return self.request('/mcp/jobs',req,headers=headers)
 def _bot_tool_turn(self,tool,args,text='Erledigt.'):
  self.wait_idle();Model.script=[[{'tool_calls':[{'index':0,'id':'call_jobs_'+str(time.monotonic_ns()),'function':{'name':tool,'arguments':json.dumps(args)}}]}],[text]]
  self.request('/api/message',{'text':'Führe das Jobs-Tool aus','bots':['test']})
  for _ in range(150):
   if not Model.script and not self.request('/api/state')['busy']:break
   time.sleep(.05)
  self.wait_idle()
 def test_17a_mcp_schema_order_and_ui_shared_service(self):
  self.wait_idle()
  self.error(401,'/mcp/jobs',{'jsonrpc':'2.0','id':1,'method':'tools/list'})
  v=self._mcp('tools/list')['result']['tools']
  names=[t['name'] for t in v]
  self.assertEqual(names,['jobs_list','jobs_get','jobs_create','jobs_update','jobs_delete','jobs_pause','jobs_resume','jobs_run_now','jobs_history'])
  for t in v:self.assertIn('Example:',t['description']);self.assertFalse(t['inputSchema'].get('additionalProperties',True))
  self.assertEqual(names,[t['name'] for t in self._mcp('tools/list')['result']['tools']])
  meta={'io.modelcontextprotocol/protocolVersion':'2026-07-28','io.modelcontextprotocol/clientCapabilities':{}}
  discover=self._mcp('server/discover',{'_meta':meta})['result']
  self.assertIn('2026-07-28',discover['supportedVersions'])
  self.assertEqual(discover['resultType'],'complete')
  new=self._mcp('tools/list',{'_meta':meta})['result']
  self.assertEqual(new['resultType'],'complete')
  self.assertEqual(new['_meta']['io.modelcontextprotocol/serverInfo']['name'],'mag-jobs')
  arg={'name':'MCP Nutzer','prompt':'Antwort mit OK','schedule':{'kind':'weekdays','time':'08:00'},'idempotency_key':'mcp-user-20261016'}
  out=self._mcp('tools/call',{'name':'jobs_create','arguments':arg})['result']
  self.assertFalse(out['isError'])
  j=json.loads(out['content'][0]['text']);self.assertEqual(j['created_by'],'user')
  modern=self._mcp('tools/call',{'_meta':meta,'name':'jobs_get','arguments':{'id':j['id']}})['result']
  self.assertEqual(json.loads(modern['content'][0]['text']),j)
  ui=self._jget(j['id']);self.assertEqual(ui,j)
  self.assertEqual(ui['schedule'],{'type':'daily','time':'08:00','days':[1,2,3,4,5]})
  self.assertIn(j,self.request('/api/jobs',method='GET')['jobs'])
  self.assertFalse(self._mcp('tools/call',{'name':'jobs_pause','arguments':{'id':j['id']}})['result']['isError'])
  self.assertTrue(self._jget(j['id'])['paused'])
  self.assertTrue(self.request('/api/jobs/'+j['id'],method='DELETE')['deleted'])
 def test_17b_bot_create_idempotency_and_secret_boundary(self):
  self.wait_idle()
  args={'name':'Von Bot','prompt':'Bot Erinnerung','schedule':{'kind':'webhook'},'idempotency_key':'bot-create-once-20261016'}
  try:
   self._bot_tool_turn('jobs_create',args)
   matches=[j for j in self.request('/api/jobs',method='GET')['jobs'] if j['name']=='Von Bot']
   self.assertEqual(len(matches),1);j=matches[0]
   self.assertEqual(j['created_by'],'bot');self.assertEqual(j['created_by_bot'],'test')
   self.assertTrue(j['webhook']);self.assertNotIn('secret',j)
   self.assertNotIn('hook_hash',json.dumps(j))
   self._bot_tool_turn('jobs_create',args)
   self.assertEqual(len([x for x in self.request('/api/jobs',method='GET')['jobs'] if x['name']=='Von Bot']),1)
   self._restart()
   self._bot_tool_turn('jobs_create',args)
   self.assertEqual(len([x for x in self.request('/api/jobs',method='GET')['jobs'] if x['name']=='Von Bot']),1)
   self.assertEqual(self._jget(j['id'])['id'],j['id'])
   self._bot_tool_turn('jobs_delete',{'id':j['id']})
   self.error(404,'/api/jobs/'+j['id'],method='GET')
  finally:
   for v in self.request('/api/jobs',method='GET')['jobs']:
    if v['name']=='Von Bot':self.request('/api/jobs/'+v['id'],method='DELETE')
   Model.script=[]
 def test_17c_bot_cannot_delete_foreign_and_malformed_call_is_not_executed(self):
  self.wait_idle();j=self._job(name='Nur Mensch')
  try:
   self._bot_tool_turn('jobs_delete',{'id':j['id']})
   self.assertEqual(self._jget(j['id'])['created_by'],'user')
   self.assertTrue(any('Fremden Job' in str(p) for p in Model.calls[-1]['messages']))
   n=len(Model.calls)
   Model.script=[[{'content':'Kein Aufruf.', 'tool_calls':[{'index':0,'id':'bad_call','function':{'name':'jobs_delete','arguments':'{'}}]}]]
   self.request('/api/message',{'text':'Unlesbarer Tool Call','bots':['test']})
   for _ in range(100):
    if len(Model.calls)>n and not self.request('/api/state')['busy']:break
    time.sleep(.05)
   self.wait_idle()
   self.assertEqual(len(Model.calls),n+1,'malformed DSML must not trigger retry')
   self.assertEqual(self._jget(j['id'])['id'],j['id'])
  finally:
   self.request('/api/jobs/'+j['id'],method='DELETE');Model.script=[]


class SingleBot(unittest.TestCase):
 """MAG_ROOM_SINGLE_BOT: exactly one bot; other stored bots are parked, never lost."""
 @classmethod
 def setUpClass(cls):
  cls.tmp=tempfile.TemporaryDirectory();cls.state=pathlib.Path(cls.tmp.name)/'state';cls.state.mkdir()
  cls.state.joinpath('agents.json').write_text(json.dumps([{'id':'alt','name':'Alter Bot','vm':'','memory':[{'text':'bleibt erhalten'}]}]))
  cls.model=http.server.ThreadingHTTPServer(('127.0.0.1',0),Model);threading.Thread(target=cls.model.serve_forever,daemon=True).start();cls.ui=port();cls.bridge=port()
  env=dict(os.environ,MAG_OWNER_CANONICAL_LOGIN='chris@github',MAG_TEST_ALLOW_LOCAL_OWNER='1',MAG_ROOM_STATE=str(cls.state),MAG_ROOM_PORT=str(cls.ui),MAG_ROOM_BRIDGE_PORT=str(cls.bridge),MAG_ROOM_BRIDGE_IP='127.0.0.1',MAG_ROOM_BACKEND=f'http://127.0.0.1:{cls.model.server_port}/v1/chat/completions',MAG_ROOM_UI=str(ROOT/'mag-room/web/index.html'),MAG_ROOM_STATIC=str(ROOT/'mag-room/static'),MAG_ROOM_MODEL_STATUS=str(pathlib.Path(cls.tmp.name)/'model-status.json'),MAG_NETMODE=str(NETMODE),MAG_TEST_NO_MEMGIT='1',MAG_JOBS_ALLOW_SECONDS='1',MAG_JOBS_TICK_MS='200',MAG_JOBS_BACKOFF_S='1',MAG_GITHUB_API=f'http://127.0.0.1:{GHPORT}',MAG_ROOM_SINGLE_BOT='deepseek-chat',MAG_ROOM_MCP_AUTO='0')
  cls.env=env
  cls.log=open(pathlib.Path(cls.tmp.name)/'server.log','a');cls.proc=subprocess.Popen([str(ROOT/'build/magroom')],env=env,stdout=cls.log,stderr=cls.log)
  for _ in range(100):
   try:Native.request.__func__(cls,'/healthz',raw=True);return
   except Exception:time.sleep(.03)
  raise RuntimeError('Native startup failed')
 @classmethod
 def tearDownClass(cls):
  cls.proc.terminate();cls.proc.wait(timeout=5);cls.log.close();cls.model.shutdown();cls.tmp.cleanup()
 request=classmethod(Native.request.__func__)
 error=Native.error
 def test_only_one_bot_and_others_parked(self):
  st=self.request('/api/state')
  self.assertEqual([a['id'] for a in st['agents']],['deepseek-chat']);self.assertEqual(st['single_bot'],'deepseek-chat')
  self.assertEqual(st['agents'][0]['vm'],'')
  stored={a['id']:a for a in json.loads(self.state.joinpath('agents.json').read_text())}
  self.assertEqual(stored['alt']['memory'],[{'text':'bleibt erhalten'}],'Parked bot must keep its data')
  self.error(409,'/api/agents',{'name':'Zweiter'})
  self.error(409,'/api/agents/deepseek-chat',method='DELETE')
  self.error(404,'/api/agents/alt',method='DELETE')
 def test_selected_model_is_used_by_same_bot_without_restart(self):
  q4='deepseek-v4-flash-0731';iq3='deepseek-v4-flash-0731-iq3'
  pid=self.proc.pid
  try:
   for choice in (iq3,q4,iq3):
    self.assertEqual(self.request('/api/model',{'id':choice})['model'],choice)
    st=self.request('/api/state')
    self.assertEqual(len(st['agents']),1)
    self.assertEqual(st['agents'][0]['model'],choice)
    before=len(Model.calls)
    self.request('/api/message',{'text':'Ein Bot, ein warmes Modell','bots':['alt']})
    for _ in range(200):
     if len(Model.calls)>before and not self.request('/api/state')['busy']:break
     time.sleep(.05)
    self.assertGreater(len(Model.calls),before)
    self.assertEqual(Model.calls[-1]['model'],choice,'must send selected model ID; never fallback')
    self.assertEqual(self.proc.pid,pid,'model selection must not restart room')
   self.error(400,'/api/model',{'id':'unknown'})
   self.assertEqual(self.request('/api/model')['model'],iq3)
  finally:
   self.request('/api/model',{'id':q4})
 def test_message_always_goes_to_the_single_bot(self):
  before=len(Model.calls)
  self.request('/api/message',{'text':'Hallo','bots':['alt']})
  for _ in range(100):
   if any(m.get('bot')=='deepseek-chat' or m.get('name')=='DeepSeek Chat' for m in self.request('/api/state')['room'] if m.get('role')=='assistant'):break
   time.sleep(.05)
  else:self.fail('Single bot did not answer')
  self.assertGreater(len(Model.calls),before)
  self.request('/api/agent/deepseek-chat/remember',{'fact':'neu'})
  stored={a['id']:a for a in json.loads(self.state.joinpath('agents.json').read_text())}
  self.assertIn('alt',stored,'Saving must not drop parked bots')
 def test_frontend_single_bot_contract(self):
  html=self.request('/',raw=True).decode()
  self.assertIn("[hidden]{display:none!important}",html)
  for needle in ('id="browser-back"','id="browser-forward"','id="browser-reload"','id="browser-nav-form"',"browserNav('back')","browserNav('forward')","syncBrowserUrl",'id="browser-overlay"',"event.type==='browser_action'",'bo-ripple'):self.assertIn(needle,html)
  for needle in ("html-preview","sandbox','allow-scripts'","$('message').focus()","function focusComposer(","e.key==='Enter'&&!e.shiftKey"):self.assertIn(needle,html)
  for needle in ("state.single_bot","$('add-agent').hidden=!!single","a.id===single"):self.assertIn(needle,html)
class SingleBotVM(unittest.TestCase):
 """MAG_ROOM_SINGLE_BOT_VM=on: the single bot gets its own VM (full sandbox) via the normal spawn path."""
 @classmethod
 def setUpClass(cls):
  if subprocess.run(['sudo','-n','true'],capture_output=True).returncode!=0:raise unittest.SkipTest('needs sudo -n (spawn runs via sudo -n AGENT_CTL)')
  cls.tmp=tempfile.TemporaryDirectory();cls.state=pathlib.Path(cls.tmp.name)/'state';cls.state.mkdir()
  cls.ctl=pathlib.Path(cls.tmp.name)/'ctl';cls.ctl.write_text('#!/bin/sh\necho "$@" >> "$(dirname "$0")/ctl.log"\n');cls.ctl.chmod(0o755)
  cls.state.joinpath('agents.json').write_text(json.dumps([{'id':'alt','name':'Alter Bot','vm':'agent1','memory':[]}]))
  cls.model=http.server.ThreadingHTTPServer(('127.0.0.1',0),Model);threading.Thread(target=cls.model.serve_forever,daemon=True).start();cls.ui=port();cls.bridge=port()
  env=dict(os.environ,MAG_OWNER_CANONICAL_LOGIN='chris@github',MAG_TEST_ALLOW_LOCAL_OWNER='1',MAG_ROOM_STATE=str(cls.state),MAG_ROOM_PORT=str(cls.ui),MAG_ROOM_BRIDGE_PORT=str(cls.bridge),MAG_ROOM_BRIDGE_IP='127.0.0.1',MAG_ROOM_BACKEND=f'http://127.0.0.1:{cls.model.server_port}/v1/chat/completions',MAG_ROOM_UI=str(ROOT/'mag-room/web/index.html'),MAG_ROOM_STATIC=str(ROOT/'mag-room/static'),MAG_ROOM_MODEL_STATUS=str(pathlib.Path(cls.tmp.name)/'model-status.json'),MAG_NETMODE=str(NETMODE),MAG_TEST_NO_MEMGIT='1',MAG_JOBS_ALLOW_SECONDS='1',MAG_JOBS_TICK_MS='200',MAG_JOBS_BACKOFF_S='1',MAG_GITHUB_API=f'http://127.0.0.1:{GHPORT}',MAG_ROOM_SINGLE_BOT='deepseek-chat',MAG_ROOM_SINGLE_BOT_VM='on',MAG_ROOM_AGENT_CTL=str(cls.ctl),MAG_ROOM_MCP_AUTO='0')
  cls.env=env
  cls.log=open(pathlib.Path(cls.tmp.name)/'server.log','a');cls.proc=subprocess.Popen([str(ROOT/'build/magroom')],env=env,stdout=cls.log,stderr=cls.log)
  for _ in range(100):
   try:Native.request.__func__(cls,'/healthz',raw=True);return
   except Exception:time.sleep(.03)
  raise RuntimeError('Native startup failed')
 @classmethod
 def tearDownClass(cls):
  cls.proc.terminate();cls.proc.wait(timeout=5);cls.log.close();cls.model.shutdown();cls.tmp.cleanup()
 request=classmethod(Native.request.__func__)
 def test_single_bot_gets_own_vm_not_a_parked_one(self):
  for _ in range(100):
   vm=self.request('/api/state')['agents'][0]['vm']
   if vm:break
   time.sleep(.05)
  self.assertEqual(vm,'agent2','must not reuse the parked bot VM agent1')
  self.assertNotIn('pending_vm',self.request('/api/state')['agents'][0],
                   'single-bot VM must publish completed lifecycle')
  self.assertIn('spawn agent2',(self.ctl.parent/'ctl.log').read_text())
  stored={a['id']:a for a in json.loads(self.state.joinpath('agents.json').read_text())}
  self.assertEqual(stored['deepseek-chat']['vm'],'agent2');self.assertEqual(stored['alt']['vm'],'agent1')
 def test_zz_missing_vm_metadata_is_quarantined_and_replaced_without_deletion(self):
  # Restore from a room snapshot that still says VM=agent2, but whose
  # registry metadata was removed externally. The old disk/VM must not
  # be destroyed, and the single bot must use a fresh identity.
  cls=type(self)
  cls.proc.terminate();cls.proc.wait(timeout=5);cls.log.close()
  cls.ctl.write_text('#!/bin/sh\necho "$@" >> "$(dirname "$0")/ctl.log"\n'
                     'if [ "$1" = verify ] && [ "$2" = agent2 ]; then '
                     'echo MAG_VM_METADATA_MISSING agent2;exit 3;fi\n')
  cls.ctl.chmod(0o755)
  previous_log_size=len((cls.ctl.parent/'ctl.log').read_text())
  before=[{'id':'alt','name':'Alter Bot','vm':'agent1','memory':[]},
          {'id':'deepseek-chat','name':'DeepSeek Chat','vm':'agent2',
           'memory':[{'text':'Benutzerdaten erhalten','src':'Nutzer'}]}]
  cls.state.joinpath('agents.json').write_text(json.dumps(before))
  cls.log=open(pathlib.Path(cls.tmp.name)/'server.log','a')
  cls.proc=subprocess.Popen([str(ROOT/'build/magroom')],env=cls.env,
                            stdout=cls.log,stderr=cls.log)
  for _ in range(200):
   try:
    actual=self.request('/api/state')['agents'][0]
    if actual.get('vm')=='agent3':break
   except Exception:pass
   time.sleep(.05)
  else:self.fail('missing registry was not recovered to a new VM')
  records={x['id']:x for x in json.loads(cls.state.joinpath('agents.json').read_text())}
  bot=records['deepseek-chat']
  self.assertEqual(bot['vm'],'agent3')
  self.assertEqual(bot['unavailable_vm'],'agent2')
  self.assertEqual(bot['memory'],before[1]['memory'])
  self.assertEqual(records['alt'],before[0])
  log=(cls.ctl.parent/'ctl.log').read_text()[previous_log_size:]
  self.assertIn('verify agent2',log)
  self.assertIn('spawn agent3',log)
  self.assertNotIn('despawn agent2',log)
  self.assertNotIn('spawn agent2',log)
# Fake VM lifecycle: "slow" holds the spawn for N seconds, "exists" lists VM
# names magbot reports as already present, "fail" makes every spawn fail.
MULTIBOT_CTL="""#!/bin/sh
d="$(dirname "$0")"
echo "$@" >> "$d/ctl.log"
if [ "$1" = preflight ]; then
 if [ -f "$d/fail-preflight" ]; then echo "preflight failed: $(cat "$d/fail-preflight")" >&2; exit 1; fi
 exit 0
fi
if [ "$1" = spawn ]; then
 [ -f "$d/slow" ] && sleep "$(cat "$d/slow")"
 if grep -qx "$2" "$d/exists" 2>/dev/null; then echo "Bot already exists" >&2; exit 1; fi
 [ -f "$d/fail" ] && { echo "Warm golden image required" >&2; exit 1; }
fi
exit 0
"""
class MultiBot(unittest.TestCase):
 """One global room, several bots; each bot may use its own model (Q4/IQ3) without touching resident models."""
 Q4='deepseek-v4-flash-0731';IQ3='deepseek-v4-flash-0731-iq3'
 # Lane mechanics are checked with simultaneous starts; StaggeredLanes covers the default.
 EXTRA_ENV={'MAG_ROOM_STAGGER':'off'}
 @classmethod
 def setUpClass(cls):
  cls.tmp=tempfile.TemporaryDirectory();cls.state=pathlib.Path(cls.tmp.name)/'state';cls.state.mkdir()
  cls.ctl=pathlib.Path(cls.tmp.name)/'ctl';cls.ctl.write_text(MULTIBOT_CTL);cls.ctl.chmod(0o755)
  cls.state.joinpath('agents.json').write_text(json.dumps([{'id':'q4bot','name':'Q4 Bot','vm':'','memory':[]},{'id':'iq3bot','name':'IQ3 Bot','vm':'','model':cls.IQ3,'memory':[]}]))
  cls.model=http.server.ThreadingHTTPServer(('127.0.0.1',0),Model);threading.Thread(target=cls.model.serve_forever,daemon=True).start();cls.ui=port();cls.bridge=port()
  env=dict(os.environ,MAG_OWNER_CANONICAL_LOGIN='chris@github',MAG_TEST_ALLOW_LOCAL_OWNER='1',MAG_ROOM_STATE=str(cls.state),MAG_ROOM_PORT=str(cls.ui),MAG_ROOM_BRIDGE_PORT=str(cls.bridge),MAG_ROOM_BRIDGE_IP='127.0.0.1',MAG_ROOM_BACKEND=f'http://127.0.0.1:{cls.model.server_port}/v1/chat/completions',MAG_ROOM_UI=str(ROOT/'mag-room/web/index.html'),MAG_ROOM_STATIC=str(ROOT/'mag-room/static'),MAG_ROOM_MODEL_STATUS=str(pathlib.Path(cls.tmp.name)/'model-status.json'),MAG_NETMODE=str(NETMODE),MAG_TEST_NO_MEMGIT='1',MAG_GITHUB_API=f'http://127.0.0.1:{GHPORT}',MAG_ROOM_AGENT_CTL=str(cls.ctl),MAG_ROOM_MCP_AUTO='0')
  env.pop('MAG_ROOM_SINGLE_BOT',None);env.update(cls.EXTRA_ENV)
  cls.log=open(pathlib.Path(cls.tmp.name)/'server.log','a');cls.proc=subprocess.Popen([str(ROOT/'build/magroom')],env=env,stdout=cls.log,stderr=cls.log)
  for _ in range(100):
   try:Native.request.__func__(cls,'/healthz',raw=True);return
   except Exception:time.sleep(.03)
  raise RuntimeError('Native startup failed')
 @classmethod
 def tearDownClass(cls):
  cls.proc.terminate();cls.proc.wait(timeout=5);cls.log.close();cls.model.shutdown();cls.tmp.cleanup()
 request=classmethod(Native.request.__func__)
 error=Native.error
 wait_idle=Native.wait_idle
 def _agents(self):return {a['id']:a for a in self.request('/api/state')['agents']}
 def _ask(self,bot):
  self.wait_idle();n=len(Model.calls)
  self.request('/api/message',{'text':'Hallo '+bot,'bots':[bot]})
  for _ in range(100):
   if len(Model.calls)>n and not self.request('/api/state')['busy']:break
   time.sleep(.05)
  else:self.fail('no model call')
  return Model.calls[n]
 def test_01_all_bots_visible_with_own_model(self):
  st=self.request('/api/state');self.assertFalse(st.get('single_bot'))
  ag=self._agents();self.assertEqual(set(ag),{'q4bot','iq3bot'})
  self.assertEqual(ag['q4bot']['model'],self.Q4);self.assertEqual(ag['iq3bot']['model'],self.IQ3)
 def test_02_each_bot_talks_to_its_own_model(self):
  self.assertEqual(self._ask('iq3bot')['model'],self.IQ3)
  self.assertEqual(self._ask('q4bot')['model'],self.Q4)
 def test_03_model_route_validation(self):
  self.error(400,'/api/agents/q4bot/model',{'model':'unbekannt'})
  self.error(400,'/api/agents/q4bot/model',{})
  self.error(404,'/api/agents/gibtsnicht/model',{'model':self.Q4})
  r=self.request('/api/agents/q4bot/model',{'model':self.IQ3})
  self.assertTrue(r['ok']);self.assertFalse(r['restart'])
  self.assertEqual(self._ask('q4bot')['model'],self.IQ3)
  self.assertEqual(json.loads(self.state.joinpath('agents.json').read_text())[0]['model'],self.IQ3)
  self.request('/api/agents/q4bot/model',{'model':self.Q4})
  self.assertEqual(self._ask('q4bot')['model'],self.Q4)
 def test_04_create_bot_with_model_and_clean_despawn(self):
  if subprocess.run(['sudo','-n','true'],capture_output=True).returncode!=0:
   b=self.request('/api/agents',{'name':'Chat Only','vm':False,'model':self.IQ3})
   self.assertEqual(b['model'],self.IQ3)
   self.request('/api/agents/'+b['id'],method='DELETE');self.assertNotIn(b['id'],self._agents());return
  b=self.request('/api/agents',{'name':'Dritter Bot','model':self.IQ3})
  self.assertEqual(b['model'],self.IQ3);self.assertTrue(b['spawning']);vm=b['pending_vm']
  a=self._ready(b['id']);self.assertEqual(a['vm'],vm)
  log=(self.ctl.parent/'ctl.log').read_text();self.assertIn('spawn '+vm,log)
  self.request('/api/agents/'+b['id'],method='DELETE')
  self.assertNotIn(b['id'],self._agents())
  self.assertIn('despawn '+vm,(self.ctl.parent/'ctl.log').read_text())
  self.assertEqual(self.request('/api/state')['agents'][0]['id'] in ('q4bot','iq3bot'),True)
 def _ready(self,bot,timeout=10):
  for _ in range(int(timeout/.05)):
   a=self._agents().get(bot)
   if a and a.get('vm') and not a.get('spawning'):return a
   time.sleep(.05)
  self.fail('VM never became ready: %r'%self._agents().get(bot))
 def _sudo(self):
  if subprocess.run(['sudo','-n','true'],capture_output=True).returncode!=0:self.skipTest('needs sudo -n for the fake lifecycle')
 def _flag(self,name,value=None):
  f=self.ctl.parent/name
  if value is None:f.unlink(missing_ok=True)
  else:f.write_text(value)
 def test_04b_spawn_never_blocks_room(self):
  self._sudo();self._flag('slow','3')
  try:
   t=time.time();b=self.request('/api/agents',{'name':'Langsam'})
   self.assertLess(time.time()-t,2);self.assertTrue(b['spawning']);self.assertEqual(b['vm'],'')
   t=time.time();st=self.request('/api/state');self.assertLess(time.time()-t,1)
   a={x['id']:x for x in st['agents']}[b['id']];self.assertTrue(a['spawning']);self.assertEqual(a['vm'],'')
   self._ask('q4bot')
   self.assertEqual(self._ready(b['id'])['vm'],b['pending_vm'])
   saved={x['id']:x for x in json.loads(self.state.joinpath('agents.json').read_text())}[b['id']]
   self.assertEqual(saved['vm'],b['pending_vm']);self.assertNotIn('pending_vm',saved)
  finally:self._flag('slow')
  self.request('/api/agents/'+b['id'],method='DELETE')
 def test_04c_delete_during_spawn_removes_vm(self):
  self._sudo();self._flag('slow','2')
  try:
   b=self.request('/api/agents',{'name':'Kurzlebig'});vm=b['pending_vm']
   self.request('/api/agents/'+b['id'],method='DELETE');self.assertNotIn(b['id'],self._agents())
   for _ in range(100):
    if 'despawn '+vm in (self.ctl.parent/'ctl.log').read_text():break
    time.sleep(.05)
   else:self.fail('VM of a removed bot was left behind')
  finally:self._flag('slow')
 def test_04cc_deleted_pending_vm_name_stays_reserved(self):
  self._sudo();self._flag('slow','2')
  try:
   old=self.request('/api/agents',{'name':'Delete while creating'})
   self.request('/api/agents/'+old['id'],method='DELETE')
   new=self.request('/api/agents',{'name':'Delete while creating'})
   self.assertNotEqual(old['pending_vm'],new['pending_vm'])
   self._ready(new['id'])
  finally:self._flag('slow')
  self.request('/api/agents/'+new['id'],method='DELETE')
 def test_04d_orphan_vm_name_is_skipped(self):
  self._sudo()
  used={a.get('vm') for a in self._agents().values()}
  orphan=next('agent%d'%n for n in range(1,50) if 'agent%d'%n not in used)
  self._flag('exists',orphan+'\n')
  try:
   b=self.request('/api/agents',{'name':'Nach Waise'});self.assertEqual(b['pending_vm'],orphan)
   a=self._ready(b['id']);self.assertNotEqual(a['vm'],orphan);self.assertTrue(a['vm'].startswith('agent'))
   self.assertIn('despawn '+orphan,(self.ctl.parent/'ctl.log').read_text())
  finally:self._flag('exists')
  self.request('/api/agents/'+b['id'],method='DELETE')
 def test_04e_failed_spawn_removes_bot_and_reports(self):
  self._sudo();self._flag('fail','1')
  try:
   b=self.request('/api/agents',{'name':'Kaputt'})
   for _ in range(100):
    if b['id'] not in self._agents():break
    time.sleep(.05)
   else:self.fail('bot with failed VM stayed')
  finally:self._flag('fail')
  self.assertNotIn(b['id'],[a['id'] for a in json.loads(self.state.joinpath('agents.json').read_text())])
  self.assertIn('despawn '+b['pending_vm'],(self.ctl.parent/'ctl.log').read_text())
  self.log.flush();self.assertIn('golden',(pathlib.Path(self.tmp.name)/'server.log').read_text(errors='replace'))
 def test_04g_chat_only_bot_gets_a_vm_and_survives_failure(self):
  self._sudo()
  b=self.request('/api/agents',{'name':'Nur Chat','vm':False});self.assertEqual(b['vm'],'')
  self._flag('fail','1')
  try:
   self.assertTrue(self.request('/api/agents/'+b['id']+'/vm',{})['spawning'])
   for _ in range(100):
    a=self._agents().get(b['id'])
    if not a or not a.get('spawning'):break
    time.sleep(.05)
   self.assertIsNotNone(a,'a failed VM must not remove the bot');self.assertEqual(a['vm'],'')
  finally:self._flag('fail')
  saved={x['id']:x for x in json.loads(self.state.joinpath('agents.json').read_text())}[b['id']]
  self.assertNotIn('pending_vm',saved);self.assertNotIn('vm_attach',saved)
  self.request('/api/agents/'+b['id']+'/vm',{})
  self.assertTrue(self._ready(b['id'])['vm'].startswith('agent'))
  self.error(409,'/api/agents/'+b['id']+'/vm',{})
  self.error(404,'/api/agents/gibtsnicht/vm',{})
  self.request('/api/agents/'+b['id'],method='DELETE')
 def test_04f_preflight_failure_leaves_nothing(self):
  self._sudo()
  before=set(self._agents())
  logp=self.ctl.parent/'ctl.log'
  start=logp.stat().st_size if logp.exists() else 0
  for step in ('kvm','golden','network','datadir','unit'):
   self._flag('fail-preflight',step)
   try:self.error(503,'/api/agents',{'name':'Halb '+step,'model':self.Q4})
   finally:self._flag('fail-preflight')
   self.assertEqual(before,set(self._agents()))
  new=logp.read_text()[start:]
  self.assertEqual(new.count('preflight'),5)
  self.assertNotIn('spawn ',new)
 def test_04g_unknown_model_is_rejected(self):
  before=set(self._agents())
  self.error(400,'/api/agents',{'name':'Falsches Modell','model':'nope'})
  self.assertEqual(before,set(self._agents()))
 def test_05_room_never_stops_or_reloads_models(self):
  src=(ROOT/'mag-room/native/routes.hpp').read_text()+(ROOT/'mag-room/native/magroom.cpp').read_text()
  for needle in ('mag-llm-iq3','"mag-llm"','restart mag-llm','stop mag-llm','"unload"','action=unload'):self.assertNotIn(needle,src)
 def test_06_frontend_contract(self):
  html=self.request('/',raw=True).decode()
  for needle in ('id="agent-model"','chosenBots','/model'):self.assertIn(needle,html)
 def test_07_single_bot_deploy_has_no_multibot_override(self):
  self.assertFalse((ROOT/'ops/mag-120.d/30-multibot.conf').exists())
  self.assertFalse((ROOT/'ops/deepseek/both-sockets.sh').exists())
  drop=(ROOT/'ops/mag-120.d/20-deepseek-audit.conf').read_text()
  self.assertIn('Environment=MAG_ROOM_SINGLE_BOT=auto',drop)
  dep=(ROOT/'ops/deploy.sh').read_text()
  self.assertIn('rm -f /etc/systemd/system/mag-120.service.d/30-multibot.conf',dep)
  self.assertNotIn('install -m 0644 "$SOURCE/ops/mag-120.d/30-multibot.conf"',dep)
 # Group chat: one lane per model server. Bots on different models answer at
 # the same time; bots sharing a model take turns; nothing blocks the others.
 def _idle(self,timeout=15):
  for _ in range(int(timeout/.05)):
   s=self.request('/api/state')
   if not s['busy']:return s
   time.sleep(.05)
  self.fail('room never became idle')
 def _calls_after(self,n,count,timeout):
  end=time.time()+timeout
  while time.time()<end:
   if len(Model.calls)>=n+count:return Model.calls[n:]
   time.sleep(.02)
  return Model.calls[n:]
 def _last_bot_text(self,bot):
  for m in reversed(self.request('/api/state')['room']):
   if m.get('bot')==bot:return m.get('content','')
  return None
 def test_08_bots_on_different_models_answer_in_parallel(self):
  self._idle();n=len(Model.calls);Model.delay=2
  try:
   t=time.time();self.request('/api/message',{'text':'Beide bitte','bots':['q4bot','iq3bot']})
   calls=self._calls_after(n,2,1.2)
   self.assertEqual(sorted(c['model'] for c in calls[:2]),sorted([self.Q4,self.IQ3]),'second bot waited for the first')
   st=self.request('/api/state');self.assertEqual(set(st['busy_bots']),{'q4bot','iq3bot'})
   self.assertGreaterEqual(len(st['activities']),1)
   self._idle();self.assertLess(time.time()-t,4.5)
  finally:Model.delay=0
  for b in ('q4bot','iq3bot'):self.assertEqual(self._last_bot_text(b),'Hallo aus dem Modell.')
 def test_09_free_bot_is_not_queued_behind_busy_bot(self):
  self._idle();n=len(Model.calls);Model.delay=2
  try:
   self.request('/api/message',{'text':'Lange Aufgabe','bots':['q4bot']})
   self._calls_after(n,1,1)
   r=self.request('/api/message',{'text':'Kurze Frage','bots':['iq3bot']})
   self.assertFalse(r.get('queued'),r)
   calls=self._calls_after(n,2,1)
   self.assertEqual([c['model'] for c in calls[:2]],[self.Q4,self.IQ3])
   self._idle()
  finally:Model.delay=0
 def test_10_same_model_bots_take_turns(self):
  self._idle();self.request('/api/agents/iq3bot/model',{'model':self.Q4})
  try:
   n=len(Model.calls);Model.delay=1.5
   self.request('/api/message',{'text':'Nacheinander','bots':['q4bot','iq3bot']})
   self._calls_after(n,1,1);time.sleep(.6)
   self.assertEqual(len(Model.calls)-n,1,'one model server must not run two turns at once')
   self._idle();self.assertEqual(len(Model.calls)-n,2)
  finally:
   Model.delay=0;self._idle();self.request('/api/agents/iq3bot/model',{'model':self.IQ3})
 def test_10b_cancel_waiting_sibling_does_not_stop_active_bot(self):
  self._idle();self.request('/api/agents/iq3bot/model',{'model':self.Q4})
  try:
   n=len(Model.calls);Model.delay=1.5
   self.request('/api/message',{'text':'Stop only waiting bot','bots':['q4bot','iq3bot']})
   self._calls_after(n,1,1)
   self.assertTrue(self.request('/api/cancel',{'bot':'iq3bot'})['cancelled'])
   self._idle()
   self.assertEqual(len(Model.calls)-n,1)
   self.assertEqual(self._last_bot_text('q4bot'),'Hallo aus dem Modell.')
  finally:
   Model.delay=0;self._idle();self.request('/api/agents/iq3bot/model',{'model':self.IQ3})
 def test_10c_cancel_active_sibling_allows_next_bot(self):
  self._idle();self.request('/api/agents/iq3bot/model',{'model':self.Q4})
  try:
   n=len(Model.calls);Model.delay=1.5
   self.request('/api/message',{'text':'Stop only active bot','bots':['q4bot','iq3bot']})
   self._calls_after(n,1,1)
   self.assertTrue(self.request('/api/cancel',{'bot':'q4bot'})['cancelled'])
   self._idle()
   self.assertEqual(len(Model.calls)-n,2)
   self.assertNotEqual(self._last_bot_text('q4bot'),'')
   self.assertEqual(self._last_bot_text('iq3bot'),'Hallo aus dem Modell.')
  finally:
   Model.delay=0;self._idle();self.request('/api/agents/iq3bot/model',{'model':self.IQ3})
 def test_10d_stop_while_reading_prompt_is_not_an_error(self):
  self._idle();Model.delay=1.5;events=[];room0=len(self.request('/api/state')['room'])
  def listen():
   r=urllib.request.urlopen(f'http://127.0.0.1:{self.ui}/api/events',timeout=10);r.fp.raw._sock.settimeout(4)
   try:
    while True:
     line=r.readline().decode()
     if line.startswith('data: '):events.append(json.loads(line[6:]))
   except Exception:pass
   finally:r.close()
  t=threading.Thread(target=listen,daemon=True);t.start();time.sleep(.3)
  try:
   n=len(Model.calls);self.request('/api/message',{'text':'Lies lange','bots':['q4bot']})
   self._calls_after(n,1,1);time.sleep(.3)
   self.assertTrue(self.request('/api/cancel',{'bot':'q4bot'})['cancelled'])
   self._idle()
  finally:Model.delay=0
  t.join(6)
  self.assertEqual([e for e in events if e.get('type')=='error'],[],'a stop is not an error')
  self.assertIn('cancelled',[e.get('type') for e in events])
  room=self.request('/api/state')['room'][room0:]
  self.assertEqual([m.get('role') for m in room],['user'],'no empty stub for a stop before any output')
  html=self.request('/',raw=True).decode()
  for needle in ("'Gestoppt · '",'phase-stopped','function shownText('):self.assertIn(needle,html)
 def test_11_cancel_one_bot_keeps_the_other(self):
  self._idle();n=len(Model.calls);Model.delay=2.5
  try:
   self.request('/api/message',{'text':'Beide arbeiten','bots':['q4bot','iq3bot']})
   self._calls_after(n,2,1.5)
   self.assertEqual(self.request('/api/cancel',{'bot':'q4bot'}),{'cancelled':True})
   time.sleep(.5);self.assertEqual(self.request('/api/state')['busy_bots'],['iq3bot'])
   # Writing stays possible while the other bot answers: the stopped bot's
   # lane is free, so the new message is answered right away, not queued.
   m=len(Model.calls);r=self.request('/api/message',{'text':'Weiter mit dir','bots':['q4bot']})
   self.assertFalse(r.get('queued'),r)
   self.assertEqual([c['model'] for c in self._calls_after(m,1,1)][:1],[self.Q4])
   self.assertEqual(set(self.request('/api/state')['busy_bots']),{'q4bot','iq3bot'})
   self._idle()
  finally:Model.delay=0
  room=self.request('/api/state')['room']
  q4=[m.get('content','') for m in room if m.get('bot')=='q4bot']
  self.assertEqual(q4[-1],'Hallo aus dem Modell.')
  self.assertFalse([m for m in room if m.get('work_error') or 'Backend' in m.get('content','')])
  self.assertEqual(self._last_bot_text('iq3bot'),'Hallo aus dem Modell.')
  self.assertEqual(self.request('/api/cancel',{'bot':'q4bot'}),{'cancelled':False})
 def test_12_parallel_lanes_keep_autonomy_single_owner_and_idle(self):
  # Both bots ask to continue without a successful tool result: nobody may
  # continue, and the second bot never becomes an owner.
  self._idle();self.request('/api/autotalk',{'on':True})
  try:
   n=len(Model.calls);Model.reply='Ich mache weiter [[MAG:WEITER]]'
   self.request('/api/message',{'text':'Aufgabe','bots':['q4bot','iq3bot']})
   self._calls_after(n,2,3);self._idle();calls=len(Model.calls);time.sleep(1.5)
   self.assertEqual(len(Model.calls),calls,'autonomy continued without verified progress')
   room=self.request('/api/state')['room']
   start=max(i for i,m in enumerate(room) if m.get('role')=='user' and m.get('content')=='Aufgabe')
   turns=[m.get('bot') for m in room[start+1:] if m.get('role')=='assistant']
   self.assertEqual(sorted(turns),['iq3bot','q4bot'])
   self.assertFalse(self.request('/api/state')['busy'])
  finally:
   Model.reply=None;self.request('/api/autotalk',{'on':False})
 def test_12b_old_progress_cannot_continue_new_owner(self):
  self._idle();self.request('/api/autotalk',{'on':True})
  call={'tool_calls':[{'index':0,'id':'remember-old','function':{'name':'remember','arguments':'{"fact":"old request progress"}'}}]}
  Model.script=[[call],[{'_sleep':2},'Old completed [[MAG:WEITER]]'],['New completed'],['Old final [[MAG:ENDE]]']]
  try:
   n=len(Model.calls)
   self.request('/api/message',{'text':'Old task','bots':['q4bot']})
   self._calls_after(n,2,2)
   time.sleep(.1)
   self.request('/api/message',{'text':'New task','bots':['iq3bot']})
   self._calls_after(n,3,2)
   self._idle();time.sleep(.5)
   self.assertEqual([c['model'] for c in Model.calls[n:]],[self.Q4,self.Q4,self.IQ3,self.Q4],'continuation must stay inside its original bot turn')
  finally:
   Model.script=[];self.request('/api/autotalk',{'on':False});self._idle()
 def test_13_frontend_lane_contract(self):
  html=self.request('/',raw=True).decode()
  for needle in ("api('/api/cancel',id?{bot:id}:{})",'id="bot-lanes"','Alle stoppen','setBusyBots(next.busy_bots,next.waiting_bots)',"event.type==='idle'&&Array.isArray(event.busy_bots)","event.type==='cancelled'&&event.bot",'state.activities'):self.assertIn(needle,html)
  # Composer bar: round +, pill with mic, one round send button that stops
  # only while the field is empty, and turns back into "Senden" on input.
  for needle in ('<form id="send-form" class="gbar">','id="plus"','class="composer-card gbar-pill"','id="mic"','id="send-button"','id="plus-think"','id="upload-camera"','id="upload-screen"',
                 "const stop=!typed&&ids.length>0&&can('stop')","ids.length>1?'Alle stoppen':'Stoppen'","dataset.mode==='stop'){e.preventDefault();stopBot('')}",
                 "$('message').addEventListener('input',fitMessageHeight)","syncComposerButton()}"):self.assertIn(needle,html)
  form=html[html.index('<form id="send-form"'):html.index('</form>',html.index('<form id="send-form"'))]
  self.assertNotRegex(form,r'<textarea[^>]*\bdisabled','the message field is never blocked')
  self.assertNotIn("message').disabled",html)
  self.assertNotIn('id="stop-generation"',html)
  for gone in ('is-stop','syncSendButton'):self.assertNotIn(gone,html)
class StaggeredLanes(unittest.TestCase):
 """Default group chat: the second model starts after the first bot has read its context, never both at once."""
 Q4=MultiBot.Q4;IQ3=MultiBot.IQ3
 EXTRA_ENV={'MAG_ROOM_STAGGER_PAUSE_MS':'300'}
 setUpClass=classmethod(MultiBot.setUpClass.__func__)
 tearDownClass=classmethod(MultiBot.tearDownClass.__func__)
 request=classmethod(Native.request.__func__)
 _idle=MultiBot._idle;_calls_after=MultiBot._calls_after;_last_bot_text=MultiBot._last_bot_text
 def tearDown(self):Model.script=[];Model.delay=0
 def _bot_msg(self,bot):
  for m in reversed(self.request('/api/state')['room']):
   if m.get('bot')==bot:return m
 def test_01_second_model_waits_for_first_thought(self):
  self._idle();n=len(Model.calls)
  Model.script=[[{'_sleep':1.0},{'reasoning_content':'Kurz '},{'reasoning_content':'**nachdenken**.'},{'_sleep':1.5},'Antwort A'],['Antwort B']]
  t=time.time();self.request('/api/message',{'text':'Hallo ihr','bots':['iq3bot','q4bot']})
  time.sleep(.6)
  self.assertEqual([c['model'] for c in Model.calls[n:]],[self.IQ3],'both models started at once')
  st=self.request('/api/state');self.assertEqual(st['waiting_bots'],['q4bot']);self.assertEqual(set(st['busy_bots']),{'iq3bot','q4bot'})
  calls=self._calls_after(n,2,3)
  self.assertEqual([c['model'] for c in calls[:2]],[self.IQ3,self.Q4])
  self.assertLess(time.time()-t,2.4,'second bot should start while the first still answers')
  self._idle();self.assertEqual(self.request('/api/state')['waiting_bots'],[])
  a=self._bot_msg('iq3bot');self.assertEqual(a['content'],'Antwort A')
  self.assertEqual(a['reasoning'],'Kurz **nachdenken**.');self.assertIsInstance(a['thought_ms'],(int,float))
  b=self._bot_msg('q4bot');self.assertEqual(b['content'],'Antwort B');self.assertNotIn('reasoning',b)
  # The reasoning is display-only: never part of a later prompt.
  m=len(Model.calls);self.request('/api/message',{'text':'Noch was','bots':['iq3bot']});self._idle()
  self.assertNotIn('nachdenken',json.dumps(Model.calls[m]['messages'],ensure_ascii=False))
 def test_02_stopping_a_waiting_bot_never_starts_it(self):
  self._idle();n=len(Model.calls)
  Model.script=[[{'_sleep':1.5},'Nur A']]
  self.request('/api/message',{'text':'Nur einer','bots':['iq3bot','q4bot']})
  time.sleep(.4);self.assertEqual(self.request('/api/state')['waiting_bots'],['q4bot'])
  self.assertEqual(self.request('/api/cancel',{'bot':'q4bot'}),{'cancelled':True})
  time.sleep(.4);st=self.request('/api/state')
  self.assertEqual(st['waiting_bots'],[]);self.assertEqual(st['busy_bots'],['iq3bot'])
  self._idle();time.sleep(.5)
  self.assertEqual([c['model'] for c in Model.calls[n:]],[self.IQ3])
  self.assertEqual(self._last_bot_text('iq3bot'),'Nur A')
 def test_03_frontend_thinking_and_waiting(self):
  html=self.request('/',raw=True).decode()
  for needle in ('function showThinking(',"head.className='think-head'","'Nachgedacht'","'Denkt nach'",'msg.reasoning','msg.thought_ms',"event.type==='bot_wait'",'wartet','next.waiting_bots','function thinkSettled(','function paintThinkPeek(',"className='think-dots'",'prefers-reduced-motion:reduce){.think-line.in'):self.assertIn(needle,html)
  self.assertNotIn("peek.textContent=text.slice(",html)
  self.assertNotIn("'Überlegungen'",html)
class SilentPhases(unittest.TestCase):
 """Minutes without model output (queued request, long tool call) must not look stuck in the UI."""
 Q4=MultiBot.Q4;IQ3=MultiBot.IQ3
 EXTRA_ENV={'MAG_ROOM_STAGGER':'off','MAG_ROOM_ALIVE_S':'1'}
 setUpClass=classmethod(MultiBot.setUpClass.__func__)
 tearDownClass=classmethod(MultiBot.tearDownClass.__func__)
 request=classmethod(Native.request.__func__)
 _idle=MultiBot._idle
 def tearDown(self):Model.script=[]
 def _events(self,seconds):
  r=urllib.request.urlopen(f'http://127.0.0.1:{self.ui}/api/events',timeout=10);out=[];t0=time.time()
  r.fp.raw._sock.settimeout(seconds)
  try:
   while time.time()-t0<seconds:
    try:line=r.readline().decode()
    except TimeoutError:break
    if line.startswith('data: '):
     e=json.loads(line[6:]);out.append(e)
     if e.get('type')=='bot_done':break
  finally:r.close()
  return out
 def test_01_alive_while_the_model_is_silent(self):
  self._idle();Model.script=[[{'_sleep':3.5},'Fertig']]
  self.request('/api/message',{'text':'Warte','bots':['q4bot']});time.sleep(.3)
  ev=self._events(5)
  alive=[e for e in ev if e.get('type')=='alive' and 'q4bot' in e.get('bots',[])]
  self.assertGreaterEqual(len(alive),2,[e.get('type') for e in ev])
  self._idle();time.sleep(1.3)
  self.assertFalse([e for e in self._events(1.5) if e.get('type')=='alive'],'no heartbeat when idle')
 def test_02_long_tool_call_is_reported_while_generated(self):
  self._idle()
  head={'tool_calls':[{'index':0,'id':'c1','function':{'name':'remember','arguments':''}}]}
  part=lambda t:{'tool_calls':[{'index':0,'function':{'arguments':t}}]}
  Model.script=[[head,part('{"text":"'),{'_sleep':1.1},part('x'*1500),{'_sleep':1.1},part('y'*100+'"}'),{'_sleep':.2}],['Gemerkt.']]
  self.request('/api/message',{'text':'Merk dir was Langes','bots':['q4bot']})
  ev=self._events(8)
  drafts=[e for e in ev if e.get('type')=='step' and e.get('phase')=='draft']
  self.assertTrue(drafts,[(e.get('type'),e.get('phase'),e.get('label')) for e in ev])
  self.assertTrue(drafts[-1]['label'].startswith('Bereitet vor: '),drafts[-1]['label'])
  self.assertIn('1.',drafts[-1]['label']);self.assertTrue(drafts[-1]['label'].endswith(' Zeichen'))
  self.assertFalse([s for s in drafts[-1].get('steps',[]) if s.startswith('Bereitet vor')],'draft is never a committed step')
  self._idle()
 def test_03_frontend(self):
  html=self.request('/',raw=True).decode()
  for needle in ("event.type==='alive'","'Keine Rückmeldung seit '","if(s.phase!=='think')s.thinking=false"):self.assertIn(needle,html)
  self.assertNotIn('hängt?',html)
class FakeMcp(http.server.BaseHTTPRequestHandler):
 """Streamable-HTTP MCP server: bearer auth, session header, paginated tools/list as SSE."""
 protocol_version='HTTP/1.1';TOKEN='secret-token-0123456789';sessions=set();calls=[];call_sessions=[];inits=0;expire=False
 def log_message(self,*a):pass
 def _send(self,code,obj=None,sse=False,headers=None):
  if obj is None:data=b''
  elif sse:data=b'event: message\ndata: '+json.dumps(obj).encode()+b'\n\n'
  else:data=json.dumps(obj).encode()
  self.send_response(code);self.send_header('Content-Type','text/event-stream' if sse else 'application/json')
  for k,v in (headers or {}).items():self.send_header(k,v)
  self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
 def do_POST(self):
  msg=json.loads(self.rfile.read(int(self.headers['Content-Length'])) or b'{}')
  cls=type(self)
  if cls.TOKEN and self.headers.get('Authorization')!='Bearer '+cls.TOKEN:return self._send(401,{'error':'unauthorized'})
  method=msg.get('method');sid=self.headers.get('Mcp-Session-Id')
  if method=='initialize':
   cls.inits+=1;new='s%d'%cls.inits;cls.sessions.add(new)
   return self._send(200,{'jsonrpc':'2.0','id':msg['id'],'result':{'protocolVersion':'2025-06-18','capabilities':{'tools':{}},'serverInfo':{'name':'fake-mcp','version':'1.2'},'instructions':'Erst lookup, dann create_note.'}},headers={'Mcp-Session-Id':new})
  if sid not in cls.sessions:return self._send(404 if sid else 400,{'error':'no session'})
  if self.headers.get('MCP-Protocol-Version')!='2025-06-18':return self._send(400,{'error':'version'})
  if method=='notifications/initialized':return self._send(202)
  if cls.expire:cls.expire=False;cls.sessions.discard(sid);return self._send(404,{'error':'gone'})
  if method=='tools/list':
   if not (msg.get('params') or {}).get('cursor'):
    res={'tools':[{'name':'lookup','title':'Nachschlagen','description':'Sucht eine Notiz.','inputSchema':{'type':'object','properties':{'q':{'type':'string'}}},'annotations':{'readOnlyHint':True}}],'nextCursor':'p2'}
   else:res={'tools':[{'name':'create_note','description':'Legt eine Notiz an.','inputSchema':{'type':'object','properties':{'text':{'type':'string'}},'required':['text']}}]}
   return self._send(200,{'jsonrpc':'2.0','id':msg['id'],'result':res},sse=True)
  if method=='tools/call':
   p=msg.get('params') or {};cls.calls.append(p);cls.call_sessions.append(sid)
   return self._send(200,{'jsonrpc':'2.0','id':msg['id'],'result':{'content':[{'type':'text','text':p['name']+' ok: '+json.dumps(p.get('arguments'),sort_keys=True)}]}},sse=True)
  self._send(200,{'jsonrpc':'2.0','id':msg.get('id'),'error':{'code':-32601,'message':'unknown'}})
class Plugins(unittest.TestCase):
 """Remote MCP servers via "+" > Plugins & MCP: secrets stay on the server, tools only for the bots that enable them."""
 Q4=MultiBot.Q4;IQ3=MultiBot.IQ3
 EXTRA_ENV={'MAG_ROOM_STAGGER':'off'}
 @classmethod
 def setUpClass(cls):
  cls.mcp=http.server.ThreadingHTTPServer(('127.0.0.1',0),FakeMcp);threading.Thread(target=cls.mcp.serve_forever,daemon=True).start()
  cls.url=f'http://127.0.0.1:{cls.mcp.server_port}/mcp'
  MultiBot.setUpClass.__func__(cls)
 @classmethod
 def tearDownClass(cls):
  MultiBot.tearDownClass.__func__(cls);cls.mcp.shutdown()
 request=classmethod(Native.request.__func__)
 error=Native.error
 _idle=MultiBot._idle
 def tearDown(self):Model.script=[]
 def _connect(self,bot='q4bot'):
  for s in self.request('/api/plugins')['servers']:
   if s['id']=='fake':return s
  return self.request('/api/plugins',{'name':'Fake','url':self.url,'auth':'bearer','token':FakeMcp.TOKEN,'bot':bot})['server']
 def _tool_names(self,call):return [t['function']['name'] for t in call.get('tools') or []]
 def _turn(self,bot,script,text='Bitte'):
  self._idle();n=len(Model.calls);Model.script=script
  self.request('/api/message',{'text':text,'bots':[bot]})
  return n
 def _wait(self,pred,timeout=8):
  end=time.time()+timeout
  while time.time()<end:
   v=pred()
   if v:return v
   time.sleep(.05)
  self.fail('timeout')
 def _tool_result(self,call):
  return '\n'.join(m.get('content') or '' for m in call['messages'] if m.get('role')=='tool')
 def test_01_connect_checks_token_and_never_leaks_it(self):
  self.error(502,'/api/plugins',{'name':'Fake','url':self.url,'auth':'bearer','token':'wrong-token-xyz'})
  self.assertEqual(self.request('/api/plugins')['servers'],[])
  self.assertFalse(list(self.state.joinpath('mcp').glob('*.secret')) if self.state.joinpath('mcp').exists() else [])
  s=self._connect()
  self.assertEqual(s['id'],'fake');self.assertEqual(s['status'],'ok');self.assertTrue(s['has_secret'])
  self.assertEqual([t['name'] for t in s['tools']],['lookup','create_note'])
  self.assertEqual([t['read_only'] for t in s['tools']],[True,False])
  self.assertEqual(s['server']['name'],'fake-mcp')
  for blob in (json.dumps(self.request('/api/plugins')),json.dumps(self.request('/api/state')),self.state.joinpath('mcp/servers.json').read_text(),self.state.joinpath('agents.json').read_text()):
   self.assertNotIn(FakeMcp.TOKEN,blob)
  sec=self.state.joinpath('mcp/fake.secret');self.assertEqual(sec.read_text(),FakeMcp.TOKEN);self.assertEqual(sec.stat().st_mode&0o777,0o600)
  ag={a['id']:a for a in self.request('/api/state')['agents']}
  self.assertEqual(ag['q4bot']['plugins'],['fake']);self.assertEqual(ag['iq3bot']['plugins'],[])
 def test_02_validation(self):
  self.error(400,'/api/plugins',{'name':'X','url':'http://example.com/mcp'})
  self.error(400,'/api/plugins',{'name':'X','url':'https://user:pw@example.com/mcp'})
  self.error(400,'/api/plugins',{'name':'X','url':self.url,'auth':'header','header':'Host','token':'abcdefgh123'})
  self.error(400,'/api/plugins',{'name':'X','url':self.url,'auth':'bearer','token':'with space 123'})
  self.error(400,'/api/plugins',{'preset':'linear'})
  self.error(404,'/api/plugins/bots/q4bot',{'server':'nope'})
  self.error(404,'/api/plugins/approvals/nope',{'decision':'allow'})
 def test_03_tools_only_for_the_bound_bot(self):
  self._connect()
  n=self._turn('q4bot',[['Ok']]);self._idle()
  names=self._tool_names(Model.calls[n]);self.assertIn('mcp__fake__lookup',names);self.assertIn('mcp__fake__create_note',names)
  d=[t for t in Model.calls[n]['tools'] if t['function']['name']=='mcp__fake__lookup'][0]['function']
  self.assertTrue(d['description'].startswith('[Plugin Fake]'));self.assertEqual(d['parameters']['properties'],{'q':{'type':'string'}})
  n=self._turn('iq3bot',[['Ok']]);self._idle()
  self.assertFalse([x for x in self._tool_names(Model.calls[n]) if x.startswith('mcp__')])
 def test_04_read_only_tool_runs_without_approval(self):
  self._connect();k=len(FakeMcp.calls)
  n=self._turn('q4bot',[[{'tool_calls':[{'index':0,'id':'c1','function':{'name':'mcp__fake__lookup','arguments':'{"q":"Einkauf"}'}}]}],['Gefunden.']])
  self._idle()
  self.assertEqual(FakeMcp.calls[k:],[{'name':'lookup','arguments':{'q':'Einkauf'}}])
  self.assertIn('lookup ok: {"q": "Einkauf"}',self._tool_result(Model.calls[-1]))
 def _approval(self):
  return self._wait(lambda:self.request('/api/state').get('approvals'))[0]
 def test_05_write_tool_waits_for_the_owner(self):
  self._connect();k=len(FakeMcp.calls)
  call=[{'tool_calls':[{'index':0,'id':'c2','function':{'name':'mcp__fake__create_note','arguments':'{"text":"Milch"}'}}]}]
  self._turn('q4bot',[call,['Nicht angelegt.']])
  a=self._approval();self.assertEqual((a['bot'],a['server'],a['tool']),('q4bot','fake','create_note'));self.assertEqual(json.loads(a['args']),{'text':'Milch'})
  time.sleep(.3);self.assertEqual(FakeMcp.calls[k:],[],'ran before approval')
  self.request('/api/plugins/approvals/'+a['id'],{'decision':'deny'});self._idle()
  self.assertEqual(FakeMcp.calls[k:],[]);self.assertIn('abgelehnt',self._tool_result(Model.calls[-1]))
  self.assertEqual(self.request('/api/state')['approvals'],[])
  self._turn('q4bot',[call,['Angelegt.']])
  a=self._approval();self.request('/api/plugins/approvals/'+a['id'],{'decision':'always'});self._idle()
  self.assertEqual(FakeMcp.calls[k:],[{'name':'create_note','arguments':{'text':'Milch'}}])
  self.assertEqual(self.request('/api/plugins')['bindings']['q4bot']['fake']['always'],['create_note'])
  self._turn('q4bot',[call,['Wieder angelegt.']]);self._idle()
  self.assertEqual(len(FakeMcp.calls[k:]),2,'"always" should skip the question')
  self.request('/api/plugins/bots/q4bot',{'server':'fake','approval':'ask'})
  self.assertEqual(self.request('/api/plugins')['bindings']['q4bot']['fake']['always'],[])
 def test_06_stopping_while_waiting_never_runs_the_tool(self):
  self._connect();k=len(FakeMcp.calls)
  self._turn('q4bot',[[{'tool_calls':[{'index':0,'id':'c3','function':{'name':'mcp__fake__create_note','arguments':'{"text":"x"}'}}]}]])
  self._approval();self.request('/api/cancel',{'bot':'q4bot'});self._idle()
  self.assertEqual(self.request('/api/state')['approvals'],[]);self.assertEqual(FakeMcp.calls[k:],[])
 def test_07_tool_switches_and_expired_session(self):
  self._connect()
  self.request('/api/plugins/bots/q4bot',{'server':'fake','off':['create_note'],'approval':'auto'})
  n=self._turn('q4bot',[['Ok']]);self._idle()
  names=self._tool_names(Model.calls[n]);self.assertIn('mcp__fake__lookup',names);self.assertNotIn('mcp__fake__create_note',names)
  inits=FakeMcp.inits;FakeMcp.expire=True;k=len(FakeMcp.calls)
  self._turn('q4bot',[[{'tool_calls':[{'index':0,'id':'c4','function':{'name':'mcp__fake__lookup','arguments':'{}'}}]}],['Ok']]);self._idle()
  self.assertEqual(FakeMcp.inits,inits+1);self.assertEqual([c['name'] for c in FakeMcp.calls[k:]],['lookup'])
  self.request('/api/plugins/bots/q4bot',{'server':'fake','off':[],'approval':'ask'})
  self.request('/api/plugins/bots/iq3bot',{'server':'fake','on':True})
  self.assertEqual({a['id']:a['plugins'] for a in self.request('/api/state')['agents']}['iq3bot'],['fake'])
  self.request('/api/plugins/bots/iq3bot',{'server':'fake','on':False})
  self.assertEqual({a['id']:a['plugins'] for a in self.request('/api/state')['agents']}['iq3bot'],[])
 def test_08_refresh_and_token_update(self):
  self._connect()
  r=self.request('/api/plugins/fake/refresh',{});self.assertTrue(r['ok']);self.assertEqual(len(r['server']['tools']),2)
  self.error(400,'/api/plugins/fake',{'token':'a b'},method='PUT')
  r=self.request('/api/plugins/fake',{'token':'other-token-123456'},method='PUT');self.assertEqual(r['server']['status'],'error')
  r=self.request('/api/plugins/fake',{'token':FakeMcp.TOKEN,'name':'Notizen'},method='PUT');self.assertEqual(r['server']['status'],'ok');self.assertEqual(r['server']['name'],'Notizen')
 def test_09_delete_removes_secret_and_bindings(self):
  self._connect();self.request('/api/plugins/bots/iq3bot',{'server':'fake','on':True})
  self.request('/api/plugins/fake',method='DELETE')
  self.assertFalse(self.state.joinpath('mcp/fake.secret').exists())
  self.assertEqual(self.request('/api/plugins')['servers'],[])
  self.assertNotIn('plugins',self.state.joinpath('agents.json').read_text())
  n=self._turn('q4bot',[['Ok']]);self._idle()
  self.assertFalse([x for x in self._tool_names(Model.calls[n]) if x.startswith('mcp__')])
 def test_10_owner_only_and_frontend(self):
  self.error(403,'/api/plugins',{'name':'X','url':self.url},headers={'Content-Type':'application/json','X-No-Token':'1'})
  html=self.request('/',raw=True).decode()
  for needle in ('id="plus-mcp"','id="mcp-dialog"',"hide('plus-mcp',!can('connectors'))",'function paintApprovals(',"event.type==='mcp_approval'","event.type==='mcp_approval_done'",'syncApprovals(next.approvals)','/api/plugins/approvals/','/^\\/(mcp|plugins?)$/i','Immer erlauben'):self.assertIn(needle,html)
  self.assertNotIn('confirm(',html.replace('magConfirm(',''))
 def test_11_session_per_bot_and_server_instructions_in_prompt(self):
  self._connect();self.request('/api/plugins/bots/iq3bot',{'server':'fake','on':True})
  look=lambda i:[[{'tool_calls':[{'index':0,'id':'s%d'%i,'function':{'name':'mcp__fake__lookup','arguments':'{}'}}]}],['Ok']]
  k=len(FakeMcp.call_sessions)
  n=self._turn('q4bot',look(1));self._idle()
  self.assertIn('[Plugin Fake]',Model.calls[n]['messages'][0]['content'])
  self.assertIn('Hinweise des Servers: Erst lookup, dann create_note.',Model.calls[n]['messages'][0]['content'])
  self._turn('iq3bot',look(2));self._idle();self._turn('q4bot',look(3));self._idle()
  q4,iq3,q4again=FakeMcp.call_sessions[k:k+3]
  self.assertNotEqual(q4,iq3,'each bot works in its own MCP session (own project)');self.assertEqual(q4,q4again)
  self.request('/api/plugins/bots/iq3bot',{'server':'fake','on':False})
  n=self._turn('iq3bot',[['Ok']]);self._idle()
  self.assertNotIn('[Plugin Fake]',Model.calls[n]['messages'][0]['content'])
  cat={c['preset']:c for c in self.request('/api/plugins')['catalog']}
  dp=cat['digitalisierungsplanung'];self.assertEqual(dp['url'],'https://accounts.digitalisierungsplanung.de/mcp/free')
  self.assertEqual(dp['auth'],'none');self.assertTrue(dp['auto_approve']);self.assertTrue(dp['auto_bind']);self.assertIn('öffentlicher Link',dp['hint'])
  self.assertNotIn('nur auf Wunsch',dp['hint'])
class FakeMcpOpen(FakeMcp):
 TOKEN='';sessions=set();calls=[];call_sessions=[];inits=0
class AutoPlugins(unittest.TestCase):
 """Digitalisierungsplanung attaches itself to every bot (chat-only included)."""
 Q4=MultiBot.Q4;IQ3=MultiBot.IQ3
 @classmethod
 def setUpClass(cls):
  cls.mcp=http.server.ThreadingHTTPServer(('127.0.0.1',0),FakeMcpOpen);threading.Thread(target=cls.mcp.serve_forever,daemon=True).start()
  cls.url=f'http://127.0.0.1:{cls.mcp.server_port}/mcp'
  cls.EXTRA_ENV={'MAG_ROOM_STAGGER':'off','MAG_ROOM_MCP_AUTO':'1','MAG_ROOM_MCP_DP_URL':cls.url}
  MultiBot.setUpClass.__func__(cls)
 @classmethod
 def tearDownClass(cls):
  MultiBot.tearDownClass.__func__(cls);cls.mcp.shutdown()
 request=classmethod(Native.request.__func__)
 _idle=MultiBot._idle
 def tearDown(self):Model.script=[]
 def _tool_names(self,call):return [t['function']['name'] for t in call.get('tools') or []]
 def _turn(self,bot,script,text='Bitte'):
  self._idle();n=len(Model.calls);Model.script=script
  self.request('/api/message',{'text':text,'bots':[bot]})
  return n
 def test_12_every_bot_gets_the_plugin_without_a_click(self):
  p=self.request('/api/plugins');srv=[s for s in p['servers'] if s.get('preset')=='digitalisierungsplanung']
  self.assertTrue(srv,p['servers']);sid=srv[0]['id']
  self.assertEqual(srv[0]['status'],'ok')
  for bot in ('q4bot','iq3bot'):
   b=p['bindings'][bot][sid];self.assertEqual(b['approval'],'auto')
  k=len(FakeMcpOpen.calls)
  n=self._turn('iq3bot',[[{'tool_calls':[{'index':0,'id':'s1','function':{'name':'mcp__digitalisierungsplanung__lookup','arguments':'{"q":"baubuch"}'}}]}],['Ok']]);self._idle()
  names=self._tool_names(Model.calls[n])
  self.assertIn('mcp__digitalisierungsplanung__lookup',names)
  sys=Model.calls[n]['messages'][0]['content']
  self.assertIn('[Plugin Digitalisierungsplanung]',sys)
  self.assertIn('zustand_share',sys)
  self.assertGreater(len(FakeMcpOpen.calls),k)
  self.assertEqual(FakeMcpOpen.calls[-1]['name'],'lookup')
class AutoPluginsOrphan(unittest.TestCase):
 """servers.json already has Digitalisierungsplanung, no bot bindings: still attach."""
 Q4=MultiBot.Q4;IQ3=MultiBot.IQ3
 @classmethod
 def setUpClass(cls):
  FakeMcpOpen.sessions=set();FakeMcpOpen.calls=[];FakeMcpOpen.call_sessions=[];FakeMcpOpen.inits=0
  cls.mcp=http.server.ThreadingHTTPServer(('127.0.0.1',0),FakeMcpOpen);threading.Thread(target=cls.mcp.serve_forever,daemon=True).start()
  cls.url=f'http://127.0.0.1:{cls.mcp.server_port}/mcp'
  cls.tmp=tempfile.TemporaryDirectory();cls.state=pathlib.Path(cls.tmp.name)/'state';cls.state.mkdir()
  cls.ctl=pathlib.Path(cls.tmp.name)/'ctl';cls.ctl.write_text(MULTIBOT_CTL);cls.ctl.chmod(0o755)
  cls.state.joinpath('agents.json').write_text(json.dumps([{'id':'q4bot','name':'Q4 Bot','vm':'','memory':[]},{'id':'iq3bot','name':'IQ3 Bot','vm':'','model':cls.IQ3,'memory':[]}]))
  mcp=cls.state/'mcp';mcp.mkdir()
  mcp.joinpath('servers.json').write_text(json.dumps([{'id':'digitalisierungsplanung','name':'Digitalisierungsplanung','url':cls.url,'auth':'none','preset':'digitalisierungsplanung','status':'ok','guide':'zustand_share','tools':[{'name':'lookup','description':'Sucht','input_schema':{'type':'object','properties':{}},'read_only':True}]}]))
  cls.model=http.server.ThreadingHTTPServer(('127.0.0.1',0),Model);threading.Thread(target=cls.model.serve_forever,daemon=True).start();cls.ui=port();cls.bridge=port()
  env=dict(os.environ,MAG_OWNER_CANONICAL_LOGIN='chris@github',MAG_TEST_ALLOW_LOCAL_OWNER='1',MAG_ROOM_STATE=str(cls.state),MAG_ROOM_PORT=str(cls.ui),MAG_ROOM_BRIDGE_PORT=str(cls.bridge),MAG_ROOM_BRIDGE_IP='127.0.0.1',MAG_ROOM_BACKEND=f'http://127.0.0.1:{cls.model.server_port}/v1/chat/completions',MAG_ROOM_UI=str(ROOT/'mag-room/web/index.html'),MAG_ROOM_STATIC=str(ROOT/'mag-room/static'),MAG_ROOM_MODEL_STATUS=str(pathlib.Path(cls.tmp.name)/'model-status.json'),MAG_NETMODE=str(NETMODE),MAG_TEST_NO_MEMGIT='1',MAG_GITHUB_API=f'http://127.0.0.1:{GHPORT}',MAG_ROOM_AGENT_CTL=str(cls.ctl),MAG_ROOM_MCP_AUTO='1',MAG_ROOM_MCP_DP_URL=cls.url,MAG_ROOM_STAGGER='off')
  env.pop('MAG_ROOM_SINGLE_BOT',None)
  cls.log=open(pathlib.Path(cls.tmp.name)/'server.log','a');cls.proc=subprocess.Popen([str(ROOT/'build/magroom')],env=env,stdout=cls.log,stderr=cls.log)
  for _ in range(100):
   try:Native.request.__func__(cls,'/healthz',raw=True);return
   except Exception:time.sleep(.03)
  raise RuntimeError('Native startup failed')
 @classmethod
 def tearDownClass(cls):
  cls.proc.terminate();cls.proc.wait(timeout=5);cls.log.close();cls.model.shutdown();cls.mcp.shutdown();cls.tmp.cleanup()
 request=classmethod(Native.request.__func__)
 _idle=MultiBot._idle
 def tearDown(self):Model.script=[]
 def _tool_names(self,call):return [t['function']['name'] for t in call.get('tools') or []]
 def _turn(self,bot,script,text='Bitte'):
  self._idle();n=len(Model.calls);Model.script=script
  self.request('/api/message',{'text':text,'bots':[bot]})
  return n
 def test_13_existing_server_with_zero_bindings_attaches_every_bot(self):
  p=self.request('/api/plugins');srv=[s for s in p['servers'] if s.get('preset')=='digitalisierungsplanung']
  self.assertTrue(srv,p['servers']);sid=srv[0]['id']
  for bot in ('q4bot','iq3bot'):
   self.assertEqual(p['bindings'][bot][sid]['approval'],'auto')
  n=self._turn('q4bot',[[{'tool_calls':[{'index':0,'id':'s1','function':{'name':'mcp__digitalisierungsplanung__lookup','arguments':'{"q":"x"}'}}]}],['Ok']]);self._idle()
  self.assertIn('mcp__digitalisierungsplanung__lookup',self._tool_names(Model.calls[n]))
  self.assertIn('HAUPTANWENDUNGSFALL / STANDARD',Model.calls[n]['messages'][0]['content'],
                'previously persisted plugin guidance must be upgraded without losing bot bindings')
class FakeTailscale(http.server.BaseHTTPRequestHandler):
 protocol_version='HTTP/1.1';calls=[]
 def log_message(self,*a):pass
 def _send(self,obj,code=200):
  data=json.dumps(obj).encode()
  self.send_response(code);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
 def _body(self):
  n=int(self.headers.get('Content-Length') or 0);return self.rfile.read(n) if n else b''
 def do_GET(self):
  self.calls.append(('GET',self.path))
  if self.path=='/api/v2/tailnet/-/devices':
   self._send({'devices':[{'id':'dev1','name':'maxim-phone','tags':['tag:mag-guest'],'addresses':['100.64.0.9'],'online':True,'lastSeen':'2026-10-17T18:00:00Z'},
                          {'id':'dev2','name':'untagged','tags':[],'addresses':['100.64.0.10']}]})
  else:self._send({'message':'not found'},404)
 def do_POST(self):
  self._body();self.calls.append(('POST',self.path))
  if self.path=='/api/v2/oauth/token':self._send({'access_token':'fake-token','expires_in':3600})
  elif self.path=='/api/v2/tailnet/-/keys':self._send({'key':'tskey-auth-fake','id':'key%d'%len(self.calls)})
  else:self._send({'message':'not found'},404)
 def do_DELETE(self):
  self.calls.append(('DELETE',self.path));self._send({})
class GuestsLive(unittest.TestCase):
 """/api/guests with Tailscale OAuth configured and a live device list (the production path)."""
 @classmethod
 def setUpClass(cls):
  cls.tmp=tempfile.TemporaryDirectory();root=pathlib.Path(cls.tmp.name);cls.state=root/'state';cls.state.mkdir()
  cls.state.joinpath('agents.json').write_text(json.dumps([{'id':'test','name':'Test','vm':'','memory':[]}]))
  base={k:['owner'] for k in ('offline','stop','jobs','memory','reset','delete','autotalk','connectors','browser')}
  cls.state.joinpath('access.json').write_text(json.dumps({'owners':['chris@github'],'owner_name':'Chris','permissions':base,
   'guest_invites':[{'id':'claimed1','key_id':'oldkey','claim_hash':'','name':'Maxim','note':'','created':int(time.time()),
                     'expires':int(time.time())+86400,'key_expires':int(time.time())+86400,'device':'dev1','node':'n1','revoked':False}]}))
  creds=root/'creds';creds.mkdir();(creds/'mag-guest-oauth').write_text(json.dumps({'client_id':'cid','client_secret':'secret'}))
  cls.whois_socket=str(root/'tailscaled.sock');cls.whois=WhoisUnix(cls.whois_socket,WhoisFixture)
  threading.Thread(target=cls.whois.serve_forever,daemon=True).start()
  cls.ts=http.server.ThreadingHTTPServer(('127.0.0.1',0),FakeTailscale);threading.Thread(target=cls.ts.serve_forever,daemon=True).start()
  cls.model=http.server.ThreadingHTTPServer(('127.0.0.1',0),Model);threading.Thread(target=cls.model.serve_forever,daemon=True).start()
  cls.ui=port();cls.bridge=port()
  env=dict(os.environ,MAG_OWNER_CANONICAL_LOGIN='chris@github',MAG_ROOM_STATE=str(cls.state),MAG_ROOM_PORT=str(cls.ui),MAG_ROOM_BRIDGE_PORT=str(cls.bridge),MAG_ROOM_BRIDGE_IP='127.0.0.1',MAG_ROOM_BACKEND=f'http://127.0.0.1:{cls.model.server_port}/v1/chat/completions',MAG_ROOM_UI=str(ROOT/'mag-room/web/index.html'),MAG_ROOM_STATIC=str(ROOT/'mag-room/static'),MAG_ROOM_MODEL_STATUS=str(root/'ms.json'),MAG_NETMODE=str(NETMODE),MAG_TEST_NO_MEMGIT='1',MAG_TAILSCALED_SOCKET=cls.whois_socket,CREDENTIALS_DIRECTORY=str(creds),MAG_TAILSCALE_API=f'http://127.0.0.1:{cls.ts.server_port}/api/v2',MAG_ROOM_MCP_AUTO='0')
  env.pop('MAG_ROOM_SINGLE_BOT',None)
  cls.log=open(root/'server.log','w');cls.proc=subprocess.Popen([str(ROOT/'build/magroom')],env=env,stdout=cls.log,stderr=cls.log)
  for _ in range(100):
   try:Native.request.__func__(cls,'/healthz',raw=True);return
   except Exception:time.sleep(.03)
  raise RuntimeError('Native startup failed')
 @classmethod
 def tearDownClass(cls):
  cls.proc.terminate();cls.proc.wait(timeout=5);cls.log.close();cls.model.shutdown();cls.ts.shutdown();cls.whois.shutdown();cls.whois.server_close();cls.tmp.cleanup()
 request=classmethod(Native.request.__func__)
 _ts=Native._ts
 def test_list_create_and_list_again_without_crash(self):
  owner=self._ts('chris@github','Chris')
  g=self.request('/api/guests',headers=owner)
  self.assertIsNone(self.proc.poll(),'room crashed on GET /api/guests')
  self.assertTrue(g['configured']);self.assertEqual(g['live_status'],'ok')
  [maxim]=g['guests'];self.assertTrue(maxim['claimed']);self.assertEqual(maxim['device_name'],'maxim-phone');self.assertTrue(maxim['online'])
  for k in ('key_id','claim_hash'):self.assertNotIn(k,maxim)
  signed={**owner,'Host':'mag.stardenburg.de','Origin':'https://mag.stardenburg.de','Sec-Fetch-Site':'same-origin'}
  made=self.request('/api/guests',{'name':'Neuer Gast','expires_at':int(time.time())+2*86400},headers=signed)
  self.assertEqual(made['name'],'Neuer Gast');self.assertIn('#invite=',made['invite_url'])
  for _ in range(3):
   g=self.request('/api/guests',headers=owner)
   self.assertIsNone(self.proc.poll(),'room crashed on GET /api/guests')
  self.assertEqual(sorted(x['name'] for x in g['guests']),['Maxim','Neuer Gast'])
  self.assertNotIn('tskey-auth-fake',json.dumps(g))
  self.assertIn(('GET','/api/v2/tailnet/-/devices'),FakeTailscale.calls)
class PendingSpawnResume(unittest.TestCase):
 """A room restart during a VM spawn adopts that VM instead of crashing or orphaning it."""
 def test_restart_adopts_recorded_vm(self):
  if subprocess.run(['sudo','-n','true'],capture_output=True).returncode!=0:self.skipTest('needs sudo -n for the fake lifecycle')
  with tempfile.TemporaryDirectory() as td:
   tmp=pathlib.Path(td);state=tmp/'state';state.mkdir();ctl=tmp/'ctl';ctl.write_text(MULTIBOT_CTL);ctl.chmod(0o755)
   (tmp/'exists').write_text('agent7\n')
   state.joinpath('agents.json').write_text(json.dumps([{'id':'neu','name':'Neu','vm':'','pending_vm':'agent7','memory':[]}]))
   model=http.server.ThreadingHTTPServer(('127.0.0.1',0),Model);threading.Thread(target=model.serve_forever,daemon=True).start();ui=port();bridge=port()
   env=dict(os.environ,MAG_OWNER_CANONICAL_LOGIN='chris@github',MAG_TEST_ALLOW_LOCAL_OWNER='1',MAG_ROOM_STATE=str(state),MAG_ROOM_PORT=str(ui),MAG_ROOM_BRIDGE_PORT=str(bridge),MAG_ROOM_BRIDGE_IP='127.0.0.1',MAG_ROOM_BACKEND=f'http://127.0.0.1:{model.server_port}/v1/chat/completions',MAG_ROOM_UI=str(ROOT/'mag-room/web/index.html'),MAG_ROOM_STATIC=str(ROOT/'mag-room/static'),MAG_ROOM_MODEL_STATUS=str(tmp/'ms.json'),MAG_NETMODE=str(NETMODE),MAG_TEST_NO_MEMGIT='1',MAG_ROOM_AGENT_CTL=str(ctl),MAG_ROOM_GUEST_RUN='/bin/false',MAG_ROOM_MCP_AUTO='0')
   env.pop('MAG_ROOM_SINGLE_BOT',None)
   log=open(tmp/'log','w');proc=subprocess.Popen([str(ROOT/'build/magroom')],env=env,stdout=log,stderr=log)
   try:
    for _ in range(200):
     saved=json.loads(state.joinpath('agents.json').read_text())[0]
     if saved.get('vm'):break
     time.sleep(.05)
    self.assertEqual(saved.get('vm'),'agent7');self.assertNotIn('pending_vm',saved)
    self.assertIsNone(proc.poll())
    self.assertNotIn('despawn',(tmp/'ctl.log').read_text())
   finally:
    proc.terminate();proc.wait(timeout=5);log.close();model.shutdown()

class DualNuma(unittest.TestCase):
 """Q4/NUMA0 and Qwen/NUMA1 retain two stable bot identities across restarts."""
 @classmethod
 def setUpClass(cls):
  cls.tmp=tempfile.TemporaryDirectory()
  cls.state=pathlib.Path(cls.tmp.name)/'state';cls.state.mkdir()
  cls.ctl=pathlib.Path(cls.tmp.name)/'ctl';cls.ctl.write_text(MULTIBOT_CTL);cls.ctl.chmod(0o755)
  cls.state.joinpath('agents.json').write_text(json.dumps([
   {'id':'primary','name':'Mein Bot','vm':'agent1','memory':[{'text':'bleibt'}]},
   {'id':'legacy','name':'Alter Bot','vm':'','memory':[{'text':'geparkt'}]}
  ]))
  cls.model=http.server.ThreadingHTTPServer(('127.0.0.1',0),Model)
  threading.Thread(target=cls.model.serve_forever,daemon=True).start()
  cls.ui=port();cls.bridge=port()
  env=dict(os.environ,MAG_OWNER_CANONICAL_LOGIN='chris@github',MAG_TEST_ALLOW_LOCAL_OWNER='1',
    MAG_ROOM_STATE=str(cls.state),MAG_ROOM_PORT=str(cls.ui),MAG_ROOM_BRIDGE_PORT=str(cls.bridge),
    MAG_ROOM_BRIDGE_IP='127.0.0.1',MAG_ROOM_BACKEND=f'http://127.0.0.1:{cls.model.server_port}/v1/chat/completions',
    MAG_ROOM_UI=str(ROOT/'mag-room/web/index.html'),MAG_ROOM_STATIC=str(ROOT/'mag-room/static'),
    MAG_ROOM_MODEL_STATUS=str(pathlib.Path(cls.tmp.name)/'ms.json'),MAG_NETMODE=str(NETMODE),
    MAG_TEST_NO_MEMGIT='1',MAG_ROOM_AGENT_CTL=str(cls.ctl),MAG_ROOM_MCP_AUTO='0',
    MAG_ROOM_SINGLE_BOT='auto',MAG_ROOM_DUAL_NUMA='1',MAG_SECONDARY_MODEL='qwen')
  cls.log=open(pathlib.Path(cls.tmp.name)/'server.log','a')
  cls.proc=subprocess.Popen([str(ROOT/'build/magroom')],env=env,stdout=cls.log,stderr=cls.log)
  for _ in range(150):
   try:
    Native.request.__func__(cls,'/healthz',raw=True)
    return
   except Exception:time.sleep(.03)
  raise RuntimeError('Dual-NUMA startup failed: '+cls.state.joinpath('agents.json').read_text())
 @classmethod
 def tearDownClass(cls):
  cls.proc.terminate();cls.proc.wait(timeout=5)
  cls.log.close();cls.model.shutdown();cls.tmp.cleanup()
 request=classmethod(Native.request.__func__)
 error=Native.error
 def test_01_exactly_two_fixed_models_and_park_legacy(self):
  s=self.request('/api/state')
  self.assertTrue(s['dual_numa']);self.assertFalse(s['single_bot'])
  self.assertEqual(len(s['agents']),2)
  self.assertEqual(s['agents'][0]['id'],'primary')
  self.assertEqual(s['agents'][0]['model'],'deepseek-v4-flash-0731')
  self.assertEqual(s['agents'][1]['model'],'qwen3.8-flash-next-uncensored')
  self.assertNotEqual(s['agents'][0]['id'],s['agents'][1]['id'])
  self.assertEqual(s['agents'][0]['vm'],'agent1')
  persisted={a['id']:a for a in json.loads(self.state.joinpath('agents.json').read_text())}
  self.assertEqual(persisted['legacy']['memory'][0]['text'],'geparkt')
 def test_02_cap_and_pinned_models(self):
  self.error(409,'/api/agents',{'name':'Dritter Bot'})
  self.error(409,'/api/agents/primary/model',{'model':'qwen3.8-flash-next-uncensored'})
  self.error(409,'/api/model',{'id':'qwen3.8-flash-next-uncensored'})
  self.error(409,'/api/agents/primary',method='DELETE')
 def test_03_rename_keeps_identity_vm_and_model(self):
  before=self.request('/api/state')['agents'][0]
  self.assertTrue(self.request('/api/agents/primary/name',{'name':'Mein eigener Bot'},method='PUT')['ok'])
  after=self.request('/api/state')['agents'][0]
  self.assertEqual(after['name'],'Mein eigener Bot')
  self.assertEqual(after['id'],before['id'])
  self.assertEqual(after['vm'],before['vm'])
  self.assertEqual(after['model'],before['model'])
  self.error(400,'/api/agents/primary/name',{'name':' '},method='PUT')

if __name__=='__main__':unittest.main(verbosity=2)
