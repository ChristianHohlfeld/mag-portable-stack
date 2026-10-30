"""Isolated native integration checks. Uses temporary state and a fake model."""
# mag-test-timeout: 300
import pathlib as _mag_path
import sys as _mag_sys
_mag_root = _mag_path.Path(__file__).resolve()
while _mag_root != _mag_root.parent and not (_mag_root / 'ops/ci/mag_deadline.py').is_file():
    _mag_root = _mag_root.parent
_mag_sys.path.insert(0, str(_mag_root / 'ops/ci'))
import mag_deadline as _mag_deadline
_mag_deadline.arm(300)
import hashlib,shlex,http.server,json,os,pathlib,socket,socketserver,subprocess,tempfile,threading,time,unittest,urllib.request,urllib.error,urllib.parse
ROOT=pathlib.Path(__file__).resolve().parents[2]
# Idle prompt prewarm adds background model calls; tests count Model.calls exactly.
os.environ.setdefault('MAG_ROOM_PREWARM','0')
# Production turns thinking off for the first user hi. The suite's explicit
# thinking switch stays in force unless a test removes this.
os.environ.setdefault('MAG_ROOM_FIRST_HI_THINK','on')
NETMODE=pathlib.Path(tempfile.mkdtemp(prefix='magnet-'))/'netmode.sh';NETLOG=NETMODE.with_suffix('.log')
NETMODE.write_text('#!/bin/sh\necho "$1" >> %s\n'%NETLOG);NETMODE.chmod(0o755)
def port():
 with socket.socket() as s:
  s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
  s.bind(('127.0.0.1',0));return s.getsockname()[1]
def wait_http(url,timeout=8,proc=None):
 deadline=time.monotonic()+timeout;last=None
 while time.monotonic()<deadline:
  if proc is not None and proc.poll() is not None:
   raise RuntimeError('server exited %s before listen %s'%(proc.returncode,url))
  try:urllib.request.urlopen(url,timeout=.4);return
  except Exception as e:
   last=e;time.sleep(.05)
 raise RuntimeError('not listening: %s (%s)'%(url,last))
# Hold GHPORT until the mock server binds so a parallel shard cannot steal it.
_GHSOCK=socket.socket();_GHSOCK.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
_GHSOCK.bind(('127.0.0.1',0));GHPORT=_GHSOCK.getsockname()[1]
def _ui_token(ui):
 import re as _re
 html=urllib.request.urlopen(f'http://127.0.0.1:{ui}/',timeout=10).read().decode()
 return _re.search(r'name="mag-token" content="([^"]+)"',html).group(1)
def _rules(content):
 i=content.index('\n[Erinnerungen]');return content[:i]
def _mem(content):
 i=content.index('\n[Erinnerungen]');return content[i+1:]
class Model(http.server.BaseHTTPRequestHandler):
 script_scope=None
 slot_snapshot=[];cache_records=[];tokenizer_fail=False;tokenizer_requests=[];restore_missing=False
 protocol_version='HTTP/1.1';calls=[];slots=[];pairs=[];paths=[];slot_delay={};delay=0;reply=None;script=[];health=200;last_prompt={};saved_prompt={};overflow_once=0;barrier_token='';barrier_release=None;hold_get=0;slot_block={}
 # A prompt that misses the pinned slot sleeps this long before any token.
 # 0 keeps the rest of the suite fast. The lock is per gateway slot, like ik.
 prefix_miss_s=0;_slot_locks={};_slot_guard=threading.Lock()
 def log_message(self,*a):pass
 def do_GET(self):
  if type(self).hold_get: time.sleep(type(self).hold_get)
  code=200
  if self.path=='/health':
   code=type(self).health
   data={200:b'{"status":"ok"}',503:b'{"error":{"code":503,"message":"Loading model","type":"unavailable_error"}}'}.get(code,b'{"error":"upstream unavailable"}')
  elif self.path=='/slots':
   snap=type(self).slot_snapshot
   if isinstance(snap,dict):snap=snap.get(self.headers.get('X-Mag-Slot'),snap.get('*',[]))
   data=json.dumps(snap).encode()
  else:data=b'{"data":[{"id":"deepseek-v4-flash-0731"}]}'
  try:
   self.send_response(code);self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
  except (BrokenPipeError,ConnectionResetError):return
 def do_POST(self):
  n=int(self.headers.get('Content-Length') or 0)
  obj=json.loads(self.rfile.read(n) if n else b'{}')
  if self.path.startswith('/slots/') and 'action=' in self.path:
   slot=self.headers.get('X-Mag-Slot')
   type(self).paths.append((self.path,slot,self.headers.get('X-Mag-Bot'),obj))
   if 'action=save' in self.path:
    type(self).saved_prompt[slot]={k:v for k,v in type(self).last_prompt.items() if isinstance(k,tuple) and k[1]==slot}
   elif 'action=restore' in self.path:
    if type(self).restore_missing and not type(self).saved_prompt.get(slot):
     data=b'{"error":"slot file missing"}'
     self.send_response(404);self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data);return
    for k,v in type(self).saved_prompt.get(slot,{}).items():type(self).last_prompt[k]=v
   data=b'{"ok":true}'
   self.send_response(200);self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data);return
  if self.path=='/tokenize':
   type(self).tokenizer_requests.append(obj.get('content',''))
   data=json.dumps({'tokens':list(obj.get('content','').encode('utf8'))}).encode()
   self.send_response(503 if type(self).tokenizer_fail else 200);self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data);return
  gate=None
  if type(self).prefix_miss_s and self.path.startswith('/v1/chat/'):
   with type(self)._slot_guard:
    gate=type(self)._slot_locks.setdefault(self.headers.get('X-Mag-Slot') or '', threading.Lock())
   if not gate.acquire(timeout=12):
    try:
     self.send_response(503);self.send_header('Content-Length','0');self.end_headers()
    except (BrokenPipeError,ConnectionResetError):return
    return
  try:
   self._complete(obj)
  finally:
   if gate:gate.release()
 def _complete(self,obj):
  self.calls.append(obj)
  type(self).slots.append(self.headers.get('X-Mag-Slot'))
  type(self).pairs.append((obj,self.headers.get('X-Mag-Slot')))  # one atomic append
  type(self).paths.append((self.path,self.headers.get('X-Mag-Slot'),self.headers.get('X-Mag-Bot'),obj))
  ev=type(self).slot_block.get(self.headers.get('X-Mag-Slot'))
  if ev is not None:ev.wait(12)
  if type(self).overflow_once:
   type(self).overflow_once-=1
   error=b'{"error":{"code":400,"message":"request (35178 tokens) exceeds the available context size (32768 tokens)","type":"exceed_context_size_error"}}'
   self.send_response(400);self.send_header('Content-Length',str(len(error)));self.end_headers();self.wfile.write(error);return
  self.send_response(200);self.send_header('Transfer-Encoding','chunked');self.end_headers()
  # Deterministic concurrency test: hold fake responses until all slots arrived.
  if type(self).barrier_release is not None and type(self).barrier_token and type(self).barrier_token in json.dumps(obj,ensure_ascii=False):
   type(self).barrier_release.wait(timeout=10)
  if self.delay:time.sleep(self.delay)
  if type(self).prefix_miss_s and obj.get('cache_prompt') is True and obj.get('id_slot') is not None:
   parts=[]
   for m in obj.get('messages') or []:
    c=m.get('content');parts.append(c if isinstance(c,str) else json.dumps(c,ensure_ascii=False,sort_keys=True))
   text='\x1e'.join(parts)
   prev=type(self).last_prompt.get((obj.get('model'),self.headers.get('X-Mag-Slot'),obj.get('id_slot')))
   if isinstance(prev,str) and prev:
    n=0;lim=min(len(prev),len(text))
    while n<lim and prev[n]==text[n]:n+=1
    # Under 98% of the resident prefix is the restart re-prefill, not a suffix.
    if n*100<len(prev)*98:time.sleep(type(self).prefix_miss_s)
  if obj.get('max_tokens')!=1 and self.headers.get('X-Mag-Slot') in type(self).slot_delay:time.sleep(type(self).slot_delay[self.headers.get('X-Mag-Slot')])
  try:
   scope=type(self).script_scope
   matches=scope is None or any(scope in m.get('content','') for m in obj.get('messages',[]) if isinstance(m.get('content'),str))
   scripted=type(self).script.pop(0) if matches and type(self).script else None
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
   cache_key=(obj.get('model'),self.headers.get('X-Mag-Slot'),slot)
   def lcp(prev):
    n=0;lim=min(len(prev),len(text))
    while n<lim and prev[n]==text[n]:n+=1
    return n
   # Like ik_llama: a pinned id_slot reuses only that slot's last prompt; an
   # unpinned request goes through the --cache-ram store (several prompts).
   if obj.get('cache_prompt') is True and slot is not None:
    prev=type(self).last_prompt.get(cache_key)
    if isinstance(prev,str):cached=lcp(prev)
    type(self).last_prompt[cache_key]=text
   elif obj.get('cache_prompt') is True:
    ram=type(self).last_prompt.setdefault('ram',[])
    cached=max([lcp(p) for p in ram]+[0]);ram.append(text)
   type(self).cache_records.append({'model':obj.get('model'),'slot':slot,'n_past':cached,'prompt_size':evaluated,'cache_prompt':obj.get('cache_prompt')})
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
  # A stale bot-published UI must be moved aside, never served.
  cls.state.joinpath('frontend.html').write_text('<!doctype html><html><head><script src="/static/message-audio-local.js"></script></head><body>BOT-OVERRIDE'+' '*300+'</body></html>')
  # Any server-side TTS would call espeak-ng; this stand-in records it.
  fakebin=pathlib.Path(cls.tmp.name)/'bin';fakebin.mkdir();cls.espeak_log=fakebin/'espeak.log'
  (fakebin/'espeak-ng').write_text('#!/bin/sh\necho "$@" >> "%s"\n'%cls.espeak_log);(fakebin/'espeak-ng').chmod(0o755)
  os.environ['PATH']=str(fakebin)+os.pathsep+os.environ.get('PATH','')
  cls.whois_socket=str(pathlib.Path(cls.tmp.name)/'tailscaled.sock')
  cls.whois=WhoisUnix(cls.whois_socket,WhoisFixture)
  threading.Thread(target=cls.whois.serve_forever,daemon=True).start()
  # Stand-in for the C++20 determinism checker: records its args, reports like the real CLI.
  cls.det=pathlib.Path(cls.tmp.name)/'mag-det';cls.det.write_text('#!/bin/sh\necho "$PWD $@" >> "$(dirname "$0")/det.log"\nsleep 1\ncase "$*" in *fail-model*) echo "[MAG-FAIL] Token-Abweichung in Lauf 4"; exit 1;; esac\necho "[MAG] Konfigurations-Fingerprint: abc123"\necho "[MAG-OK] 20/20 Token-IDs und Text identisch"\necho "         Token-Stream-Hash: deadbeef"\n');cls.det.chmod(0o755)
  cls.model=http.server.ThreadingHTTPServer(('127.0.0.1',0),Model);threading.Thread(target=cls.model.serve_forever,daemon=True).start();cls.ui=port();cls.bridge=port()
  cls.start()
 @classmethod
 def start(cls):
  last=None
  for attempt in range(6):
   if attempt:cls.ui=port();cls.bridge=port()
   env=dict(os.environ,MAG_DET_CLI=str(cls.det),MAG_OWNER_CANONICAL_LOGIN='chris@github',MAG_TEST_ALLOW_LOCAL_OWNER='1',MAG_ROOM_STATE=str(cls.state),MAG_ROOM_PORT=str(cls.ui),MAG_ROOM_BRIDGE_PORT=str(cls.bridge),MAG_ROOM_BRIDGE_IP='127.0.0.1',MAG_ROOM_BACKEND=f'http://127.0.0.1:{cls.model.server_port}/v1/chat/completions',MAG_ROOM_UI=str(ROOT/'mag-room/web/index.html'),MAG_ROOM_STATIC=str(ROOT/'mag-room/static'),MAG_ROOM_AUTOTALK_SLEEP='1',MAG_JOBS_MCP_TOKEN='local-unit-test-mcp-token-0123456789',MAG_ROOM_MODEL_STATUS=str(pathlib.Path(cls.tmp.name)/'model-status.json'),MAG_NETMODE=str(NETMODE),MAG_TEST_NO_MEMGIT='1',MAG_TEST_NO_CLEAR_BACKUP='1',MAG_JOBS_ALLOW_SECONDS='1',MAG_JOBS_TICK_MS='200',MAG_JOBS_BACKOFF_S='1',MAG_GITHUB_API=f'http://127.0.0.1:{GHPORT}',MAG_TAILSCALED_SOCKET=cls.whois_socket,MAG_ROOM_MCP_AUTO='0')
   cls.log=open(pathlib.Path(cls.tmp.name)/'server.log','a');cls.proc=subprocess.Popen([str(ROOT/'build/magroom')],env=env,stdout=cls.log,stderr=cls.log)
   try:wait_http(f'http://127.0.0.1:{cls.ui}/healthz',timeout=8,proc=cls.proc);return
   except Exception as e:
    last=e
    if cls.proc.poll() is None:
     cls.proc.terminate()
     try:cls.proc.wait(timeout=3)
     except Exception:cls.proc.kill();cls.proc.wait(timeout=3)
    cls.log.close()
  raise RuntimeError('Native startup failed: %s'%last)
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
 def test_01a_skill_library_is_a_real_model_tool(self):
  # Grok-like workflows must be executable tools, not just prose in
  # bot_context.md. Persist drafts and keep them reusable across sessions.
  self.wait_idle()
  name='CI skill persistence'
  call={'tool_calls':[{'index':0,'id':'call_skill_save','function':{
    'name':'skill','arguments':json.dumps({'action':'save','name':name,
      'when':'When asked for a quick reproducible check',
      'steps':'Read current source, perform one safe operation, report output.',
      'verify':'Check actual output and explain failures.',
      'approval':'Ask before sending, deleting or publishing.'})}}]}
  prior=len(Model.calls)
  Model.script=[[call],['Skill gespeichert.']]
  try:
   self.request('/api/message',{'text':'Speichere den getesteten Ablauf als Entwurf','bots':['test']})
   for _ in range(200):
    saved=self.state/'skills.json'
    if saved.exists() and not self.request('/api/state')['busy']:break
    time.sleep(.05)
   else:self.fail('Skill tool call did not persist within 10 seconds')
   tools=[t['function']['name'] for t in Model.calls[prior]['tools']]
   self.assertIn('skill',tools)
   skill=next(x for x in json.loads(saved.read_text()) if x['name']==name)
   self.assertEqual(skill['status'],'draft','saving must NOT auto-enable a routine')
   self.assertEqual(skill['verify'],'Check actual output and explain failures.')
   self.assertEqual(skill['approval'],'Ask before sending, deleting or publishing.')
  finally:
   Model.script=[]
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
 def test_02z_large_audio_chunked_upload_resumes_and_commits_once(self):
  # Real >64 MiB recording; no individual request exceeds 4 MiB.
  import urllib.parse
  uid='a'*32
  total=64*1024*1024+11
  name='Aufzeichnung (2).m4a'
  base='/api/upload/chunk?id='+uid
  qname=urllib.parse.quote(name)
  absent=self.request(base.replace('/chunk?','/chunk/status?'))
  self.assertEqual(absent['received'],0)
  self.assertFalse(absent['done'])
  self.assertFalse(absent['exists'])
  self.error(403,base+'&name='+qname+'&offset=0&total='+str(total),
             b'abcd',headers={'Content-Type':'application/octet-stream','X-No-Token':'1'})
  self.error(404,base.replace('/chunk?','/chunk/commit?'),b'')
  # Exercise the full 16 MiB client chunk without relaxing the 64 MiB
  # ordinary upload cap or the 512 MiB per-session total.
  buf=b'a'*(16*1024*1024)
  # Test first packet and idempotent retransmission (lost HTTP ack).
  first=base+'&name='+qname+'&offset=0&total='+str(total)
  self.assertEqual(self.request(first,buf)['received'],len(buf))
  self.assertEqual(self.request(first,buf)['received'],len(buf))
  self.error(409,first,b'z'*len(buf))
  self.error(409,base.replace('/chunk?','/chunk/commit?'),b'')
  offset=len(buf)
  while offset<total:
   data=buf[:min(len(buf),total-offset)]
   result=self.request(base+'&name='+qname+'&offset='+str(offset)+'&total='+str(total),data)
   self.assertEqual(result['received'],offset+len(data))
   offset=result['received']
  status=self.request(base.replace('/chunk?','/chunk/status?'))
  self.assertFalse(status['done']);self.assertEqual(status['received'],total)
  before=len(self.request('/api/state')['room'])
  file=self.request(base.replace('/chunk?','/chunk/commit?'),b'')
  self.assertEqual(file['size'],total)
  self.assertEqual(file['mime'],'audio/mp4')
  self.assertEqual(file['name'],'Aufzeichnung__2_.m4a')
  self.assertEqual(self.request(base.replace('/chunk?','/chunk/commit?'),b'')['stored'],file['stored'])
  self.assertTrue(self.request(base.replace('/chunk?','/chunk/status?'))['done'])
  self.assertEqual(len(self.request('/api/state')['room']),before+1)
  url='http://127.0.0.1:%d/api/file/%s'%(self.ui,file['stored'])
  for rng,want in [('bytes=0-3',b'aaaa'),('bytes=-11',b'a'*11),('bytes=67108860-67108868',b'a'*9)]:
   req=urllib.request.Request(url,headers={'Range':rng})
   with urllib.request.urlopen(req,timeout=10) as response:
    self.assertEqual(response.status,206)
    self.assertEqual(response.read(),want)
  self.assertTrue((self.state/'files'/file['stored']).is_file())
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
    self.assertEqual(Model.calls[-1]['messages'][1:],
                     [{'role':'user','content':'Sampling model '+model_id}])
    self.assertIn('[Identitaet]\nDu bist ',Model.calls[-1]['messages'][0]['content'])
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
 def test_05aa_new_model_does_not_inherit_legacy_sampling(self):
  self.wait_idle()
  models=self.request('/api/state')['models']
  first,second=[m['id'] for m in models]
  path=self.state/'model-settings.json'
  previous=path.read_bytes() if path.exists() else None
  legacy=self.state/'sampling'
  old=legacy.read_bytes() if legacy.exists() else None
  try:
   legacy.write_text('on\\n')
   path.write_text(json.dumps({first:{'deterministic':True}}))
   self.proc.terminate();self.proc.wait(timeout=5);self.log.close();self.start()
   states={m['id']:m['deterministic'] for m in self.request('/api/state')['models']}
   self.assertTrue(states[first],'explicit saved deterministic=true must survive')
   self.assertFalse(states[second],'new model must ignore old sampling=on')
  finally:
   self.proc.terminate();self.proc.wait(timeout=5);self.log.close()
   if previous is None:path.unlink(missing_ok=True)
   else:path.write_bytes(previous)
   if old is None:legacy.unlink(missing_ok=True)
   else:legacy.write_bytes(old)
   self.start()

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
  # Deterministic still sends the bot's short identity prompt, then ONE user message.
  self.assertEqual(first['messages'][1:],[{'role':'user','content':'hi'}])
  self.assertEqual(first['messages'][0]['role'],'system')
  self.assertIn('[Identitaet]\nDu bist ',first['messages'][0]['content'])
  self.assertIn('nicht Claude, nicht Anthropic',first['messages'][0]['content'])
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
   self.assertEqual([x['messages'][-1]['content'] for x in Model.calls[start:start+3]],
                    ['eins','zwei','drei'])
   self.assertTrue(all(len(x['messages'])==2 and x['messages'][0]['role']=='system' and not x.get('tools') for x in Model.calls[start:start+3]))
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
  tail=s['room'][-4:];self.assertEqual(tail[0]['content'],'N-Eins');self.assertEqual(tail[1]['content'],'');self.assertTrue(tail[1].get('interrupted'));self.assertEqual(tail[1].get('stopped'),'user')
  self.assertEqual(tail[2]['content'],'N-Zwei')
  last=Model.calls[-1]['messages'];self._roles_ok(last);self.assertNotIn('denke',json.dumps(last),'partial reasoning must not leak into history')
  self.assertIn('(abgebrochen)',json.dumps(last,ensure_ascii=False),'the model still learns the turn was interrupted by the user')
 def test_15b1_stale_send_now_is_conflict_not_missing_message(self):
  # The scheduler may claim the item after a queue chip was rendered but
  # before 'Jetzt senden' reaches the backend. Never cancel or send again.
  self.wait_idle();Model.script=[['Already delivered.']];Model.calls.clear()
  r=self.request('/api/message',{'text':'Already delivered','bots':['test'],
                                  'request_id':'stale-now-01'})
  qid=r['item']['id']
  state=self._settle(lambda s:not s['busy'] and not s['pending'] and
      any(m.get('content')=='Already delivered.' for m in s['room']))
  n=len(Model.calls);before=self.request('/api/queue')['revision']
  with self.assertRaises(urllib.error.HTTPError) as ctx:
   self.request('/api/queue/'+qid+'/now',{})
  self.assertEqual(ctx.exception.code,409)
  data=json.loads(ctx.exception.read())
  self.assertEqual(data['status'],'stale')
  self.assertEqual(data['queue']['pending'],[])
  self.assertEqual(data['queue']['revision'],before)
  self.assertEqual(len(Model.calls),n,'stale action must not launch a new turn')
  self.assertEqual(len([m for m in self.request('/api/state')['room']
       if m.get('content')=='Already delivered']),1)
  self.assertFalse(self.request('/api/state')['busy'])
  Model.script=[]

 def test_15b1a_send_now_releases_held_item_without_resuming_other_queue(self):
  # Stop intentionally holds queued messages across restart. Explicit
  # Send Now is an exception for ONE selected item, not a global resume.
  self.wait_idle();Model.script=[self._slow('out',40)]
  try:
   self.request('/api/message',{'text':'Running before Stop','bots':['test']})
   self._busy()
   first=self.request('/api/message',{'text':'Keep held','bots':['test']})['item']['id']
   selected=self.request('/api/message',{'text':'Send this now','bots':['test']})['item']['id']
   self.request('/api/stop',{})
   self.wait_idle()
   st=self.request('/api/state')
   self.assertTrue(st['queue_paused'])
   self.assertTrue(all(p['held'] for p in st['pending']))
   Model.script=[['Priority sent.']]
   result=self.request('/api/queue/'+selected+'/now',{})
   self.assertTrue(result['ok'])
   self.assertEqual(result['status'],'prioritized')
   self.assertTrue(result['queue']['paused'],'global pause remains intact')
   self.assertTrue(any(p['id']==first and p['held'] for p in result['queue']['pending']))
   delivered=self._settle(lambda x:not x['busy'] and
     any(m.get('content')=='Priority sent.' for m in x['room']))
   self.assertEqual([p['id'] for p in delivered['pending']],[first])
   self.assertTrue(delivered['pending'][0]['held'])
   self.assertEqual(len([m for m in delivered['room']
                         if m.get('content')=='Send this now']),1)
  finally:
   self.request('/api/queue',method='DELETE')
   self.request('/api/queue/resume',{})
   Model.script=[]

 def test_15b2_send_receipt_is_idempotent_after_lost_ack(self):
  # Reuse the same client request ID after a lost HTTP response; no
  # second room message, no second model job, including after dispatch.
  self.wait_idle();Model.script=[['Once only.']];Model.calls.clear()
  body={'text':'Same request once','bots':['test'],'request_id':'same-retry-42'}
  one=self.request('/api/message',body)
  duplicate=self.request('/api/message',body)
  self.assertTrue(one['accepted'])
  self.assertTrue(duplicate['duplicate'])
  state=self._settle(lambda s:not s['busy'] and not s['pending'] and
      any(m.get('content')=='Once only.' for m in s['room']))
  self.assertEqual(len([m for m in state['room']
      if m.get('content')=='Same request once']),1)
  self.assertEqual(len(Model.calls),1)
  Model.script=[]

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

 def test_16j_jobs_belong_to_one_bot_and_show_on_the_right(self):
  self.wait_idle()
  html=self.request('/',raw=True).decode()
  left=html.split('id="left-panel"',1)[1].split('<aside class="right"',1)[0]
  right=html.split('id="right-panel"',1)[1].split('</aside>',1)[0]
  self.assertNotIn('id="jobs-section"',left)
  self.assertIn('id="add-agent"',left)
  self.assertIn('Entfernen',html)
  self.assertNotIn("check.checked?selected.add",html)
  self.assertIn('id="bot-active"',right)
  self.assertIn('id="bot-detail"',right)
  self.assertIn('id="jobs-section"',right)
  self.assertIn('id="browser-section"',right)
  self.assertIn('id="terminal-panel"',right)
  self.assertIn("sessionStorage.setItem('mag-focused-bot'",html)
  self.assertIn("sessionStorage.setItem('mag-selected'",html)
  self.assertIn('function focusBot(',html)
  self.assertIn('bot:jobEdit?.bot||focusedBotId',html)
  j=None;other=None
  try:
   j=self._job(name='Morgenbericht',prompt='VOLLER-AUFTRAG-NICHT-IM-SYSTEM '+('x'*400),schedule={'type':'daily','time':'07:00'})
   self.assertEqual(j['bot'],'test')
   disk=json.loads((self.state/'jobs.json').read_text())
   self.assertEqual(next(d['bot'] for d in disk if d['id']==j['id']),'test')
   self.error(400,'/api/jobs/'+j['id'],{'bot':'fremd'},method='PUT')
   self.assertEqual(self._jget(j['id'])['bot'],'test')
   self.wait_idle();Model.calls.clear();Model.reply='Kenntnis.'
   self.request('/api/message',{'text':'Welche Fähigkeiten hast du?','bots':['test']})
   self._settle(lambda s:not s['busy'] and any(m.get('content')=='Kenntnis.' for m in s['room']))
   sysm=Model.calls[-1]['messages'][0]['content']
   user=Model.calls[-1]['messages'][1]['content']
   self.assertIn('[Fähigkeiten]',user)
   self.assertIn('Morgenbericht',user)
   self.assertIn('täglich 07:00',user)
   self.assertNotIn('VOLLER-AUFTRAG-NICHT-IM-SYSTEM',sysm)
   self.assertIn('VOLLER-AUFTRAG-NICHT-IM-SYSTEM',user)
   other=self.request('/api/agents',{'name':'Neben','vm':False})
   self.assertEqual(other['id'],'neben')
   self.error(400,'/api/jobs',{'name':'Ohne','prompt':'x','schedule':{'type':'none'}})
   fremd=self._job(name='Nebenjob',prompt='NUR-NEBEN',bot=other['id'])
   self.assertEqual(fremd['bot'],other['id'])
   self._bot_tool_turn('jobs_list',{})
   self.assertNotIn('Nebenjob',json.dumps(Model.calls[-1]['messages'],ensure_ascii=False))
   self._bot_tool_turn('jobs_get',{'id':fremd['id']})
   self.assertTrue(any('anderen Bot' in str(m) for m in Model.calls[-1]['messages']))
   self.request('/api/agents/'+other['id'],method='DELETE');other=None
   self.error(404,'/api/jobs/'+fremd['id'],method='GET')
   self.assertEqual(self._jget(j['id'])['name'],'Morgenbericht')
   self.proc.kill();self.proc.wait(timeout=5);self.log.close()
   data=json.loads((self.state/'jobs.json').read_text())
   data.append({'id':'orphan1','name':'Verwaist','prompt':'Alt','bot':'','schedule':{'type':'none'},'created_by':'user'})
   data.append({'id':'ghost1','name':'Geist','prompt':'Weg','bot':'nobody','schedule':{'type':'none'},'created_by':'user'})
   (self.state/'jobs.json').write_text(json.dumps(data))
   self.start()
   self.assertEqual(self._jget('orphan1')['bot'],'test')
   self.assertEqual(self._jget('ghost1')['bot'],'test')
   saved={d['id']:d['bot'] for d in json.loads((self.state/'jobs.json').read_text())}
   self.assertEqual(saved['orphan1'],'test')
   self.assertEqual(saved['ghost1'],'test')
  finally:
   Model.reply=None;Model.script=[]
   if other:
    try:self.request('/api/agents/'+other['id'],method='DELETE')
    except Exception:pass
   ids=(['orphan1','ghost1'] if j is None else [j['id'],'orphan1','ghost1'])
   for jid in ids:
    try:self.request('/api/jobs/'+jid,method='DELETE')
    except Exception:pass

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
   self.error(409,'/api/queue/'+ids[0]+'/move',{'before':'not-a-job'})
   html=self.request('/',raw=True).decode()
   for needle in ("queueDropAt(","queueShift(","dragstart","pointerup","/move","photo-preview","upload-photo"):
    self.assertIn(needle,html)
   self.assertIn("btn.addEventListener('wheel'",html)
   self.assertNotIn("btn.addEventListener('wheel',e=>{e.preventDefault()",html)
  finally:
   self.request('/api/queue',method='DELETE');self.request('/api/cancel',{});self.request('/api/queue/resume',{});Model.script=[]

 def test_15l_room_clear_stops_live_bot_and_queue(self):
  self.wait_idle()
  self._access(['chris@github'])
  owner=self._ts('chris@github','Chris')
  Model.script=[self._slow('out',80),['Nie.']]
  try:
   self.request('/api/message',{'text':'Clear-Busy','bots':['test']})
   self._busy()
   self.request('/api/message',{'text':'Wartet','bots':['test']})
   result=self.request('/api/room/clear',{},headers=owner)
   self.assertTrue(result['ok'])
   self.assertNotIn('undo',result)
   self.assertNotIn('snapshot',result)
   time.sleep(.4)
   state=self.request('/api/state')
   self.assertEqual(state['room'],[])
   self.assertEqual(state['files'],[])
   self.assertEqual(state['pending'],[])
   self.assertFalse(state['busy'])
   self.assertTrue(self.request('/api/room/clear',{},headers=owner)['ok'])
  finally:
   self.request('/api/queue/resume',{})
   Model.script=[]

 def test_15m_bot_rights_github_read_only_no_gitea_admin(self):
  self.request('/api/bot-settings',{'bots':['test'],'github_enabled':True},method='PUT')
  self.wait_idle();self.request('/api/connectors/github',{'token':'ghp_'+'c'*36})
  args=[{"action":"create_issue","repo":"ChristianHohlfeld/mag-portable-stack","title":"x"},
        {"action":"comment_issue","repo":"ChristianHohlfeld/mag-portable-stack","number":1,"body":"x"},
        {"action":"create_repo","name":"evil"}]
  Model.calls.clear()
  Model.script=[[{'tool_calls':[{'index':0,'id':'call_gh%d'%i,'function':{'name':'github','arguments':json.dumps(a)}}]}] for i,a in enumerate(args)]+[['Fertig.']]
  self.request('/api/message',{'text':'GH-Write','bots':['test']});self.wait_room('Fertig.');self.wait_idle()
  res=[m for m in Model.calls[-1]['messages'] if m.get('role')=='tool' and str(m.get('tool_call_id','')).startswith('call_gh')]
  self.assertEqual(len(res),3)
  for m in res:self.assertIn('nur lesbar',json.dumps(m,ensure_ascii=False))
  self.request('/api/connectors/github',method='DELETE')
  vm=(ROOT/'mag-room/native/vm.cpp').read_text()
  acct=(ROOT/'mag-room/native/gitea_account.hpp').read_text()
  auth=(ROOT/'ops/gitea-ci/bot-auth.sh').read_text()
  src=vm+acct
  create=src[src.index('gitea_args({"create"'):src.index('if (!created.code)')]
  self.assertNotIn('--admin',create,'bot Gitea accounts must never be admins')
  issued=auth[auth.index('cli create'):auth.index('generate-access-token')]
  self.assertNotIn('--admin',issued);self.assertIn('--restricted',issued)
  # issue writes the token into the guest first; commit then restricts the
  # same account. Rights must run before that account is left able to log in.
  commit=auth.split('mode == commit',1)[1].split('else',1)[0]
  self.assertIn('mag-gitea-bot-rights',commit)
  self.assertLess(commit.index('mag-gitea-bot-rights'),commit.index('prohibit_login'))
  prov=vm[vm.index('provision_git'):vm.index('undefine_domain')]
  self.assertLess(prov.index('mag-gitea-bot-auth","issue"'),prov.index('mag-gitea-bot-auth","commit"'))
  prompts=(ROOT/'mag-room/native/magroom.cpp').read_text()
  for bad in ('Administrationsaccount','is an administrator','mag-deploy/request','Runner des MAG-Hosts'):
   self.assertNotIn(bad,prompts+src+auth)
  self.assertIn('X-MCP-Readonly',(ROOT/'mag-room/native/mcp.hpp').read_text())
 def test_15k_composer_stop_cancels_turn_keeps_queue(self):
  self.wait_idle();Model.script=[self._slow('out',60),['Weiter normal.']]
  self.request('/api/message',{'text':'Stop-Test','bots':['test']});self._busy();time.sleep(1.0)
  self.request('/api/message',{'text':'Soll-weg','bots':['test']})
  self.assertEqual(self.request('/api/cancel',{})['cancelled'],True)
  s=None
  for _ in range(80):
   s=self.request('/api/state')
   room=s.get('room') or []
   if room and room[-1].get('interrupted') and not s.get('busy'): break
   time.sleep(.05)
  else:
   self.fail('partial was not marked stopped: %s'%(s['room'][-1] if s and s.get('room') else s))
  self.assertFalse(s.get('queue_paused'),'composer stop must not pause the queue')
  self.assertEqual(s.get('pending'),[],'composer stop clears the queue')
  self.assertEqual(s.get('busy_bots') or [],[])
  self.assertFalse(any(a.get('on') for a in (s.get('activities') or [])))
  part=s['room'][-1];self.assertTrue(part['content'].startswith('Wort0 '));self.assertNotIn('(abgebrochen)',part['content']);self.assertTrue(part.get('interrupted'))
  self.assertEqual(part.get('stopped'),'user');self.assertNotIn('work_error',part)
  self.assertNotIn('Soll-weg',[m.get('content') for m in s['room']])
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
  self.request('/api/bot-settings',{'bots':['test'],'github_enabled':True},method='PUT')
  seen=[]
  class GH(http.server.BaseHTTPRequestHandler):
   def log_message(self,*a):pass
   def do_GET(self):
    seen.append((self.path,self.headers.get('Authorization')));b=json.dumps([{'full_name':'chris/mag','private':True,'description':'x','extra':'drop'}]).encode()
    self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b)
  try:_GHSOCK.close()
  except OSError:pass
  srv=http.server.ThreadingHTTPServer(('127.0.0.1',GHPORT),GH);threading.Thread(target=srv.serve_forever,daemon=True).start()
  wait_http(f'http://127.0.0.1:{GHPORT}/',timeout=4)
  try:
   self.wait_idle();call={'tool_calls':[{'index':0,'id':'call_gh1','function':{'name':'github','arguments':'{"action":"list_repos"}'}}]}
   Model.calls.clear();Model.script=[[call],['Keine Verbindung.']]
   self.request('/api/message',{'text':'GH-1','bots':['test']});self.wait_idle();time.sleep(.3)
   self.assertTrue(any(t['function']['name']=='github' for t in Model.calls[0]['tools']),'github registered only after opt-in')
   self.assertIn('nicht verbunden',json.dumps(Model.calls[-1]['messages']))
   self.request('/api/connectors/github',{'token':'ghp_testtoken1234567890'})
   Model.calls.clear();Model.script=[[call],['Repos gelistet.']]
   self.request('/api/message',{'text':'GH-2','bots':['test']});self.wait_idle();time.sleep(.3)
   msgs=json.dumps(Model.calls[-1]['messages'])
   self.assertIn('chris/mag',msgs);self.assertNotIn('extra',msgs);self.assertNotIn('ghp_testtoken1234567890',msgs)
   self.assertEqual(seen[-1],('/user/repos?per_page=50&sort=updated','Bearer ghp_testtoken1234567890'))
   self.assertFalse([p for p in (self.state/'connectors').iterdir() if p.name.startswith('.gh-hdr')],'header file removed')
   self.request('/api/bot-settings',{'bots':['test'],'github_enabled':False},method='PUT')
   self.assertTrue(self.request('/api/connectors')['github']['connected'])
   Model.calls.clear();Model.script=[['Ohne GitHub.']]
   self.request('/api/message',{'text':'GH-OFF','bots':['test']});self.wait_room('Ohne GitHub.');self.wait_idle()
   self.assertFalse(any(t['function']['name']=='github' for t in Model.calls[0]['tools']))
   self.request('/api/bot-settings',{'bots':['test'],'github_enabled':True},method='PUT')
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
  self.request('/api/queue/resume',{})
  marker='Merke DeepSeek-Test '+str(time.monotonic_ns())
  first=[{'reasoning_content':'Ich merke mir das.'},{'tool_calls':[{'index':0,'id':'call_ds_1','function':{'name':'remember','arguments':'{"fact":"DeepSeek-Test"}'}}]}]
  Model.script_scope=marker
  Model.script=[first,['Notiert.']]
  try:
   initial=len(Model.calls)
   self.request('/api/message',{'text':marker,'bots':['test']})
   self.wait_room('Notiert.');self.wait_idle()
   # Assert this test's tool roundtrip, not unrelated global Model.calls.
   fresh=Model.calls[initial:]
   own=[x for x in fresh if any(marker in (msg.get('content') or '')
                             for msg in x.get('messages',[]) if isinstance(msg.get('content'),str))]
   self.assertEqual(len(own),2,repr([x.get('messages',[])[-1:] for x in fresh])[:1200])
   msgs=own[-1]['messages']
   asst=[m for m in msgs if m['role']=='assistant' and m.get('tool_calls')]
   self.assertEqual(asst[-1]['reasoning_content'],'Ich merke mir das.')
   self.assertEqual(asst[-1]['tool_calls'][0]['function']['name'],'remember')
   tool=[m for m in msgs if m['role']=='tool']
   self.assertEqual(tool[-1]['tool_call_id'],'call_ds_1')
  finally:
   try:self.wait_idle()
   finally:Model.script=[];Model.script_scope=None
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
  for needle in ('id="model-state"','id="model-banner"',"event.type==='model'","event.type==='bot_status'",'id="agent-chat-only"',"keine VM",
                 "option.textContent=a.name+(a.vm?'':' · VM startet')","if(!bot.vm){","showThinking(s.node","#model-state .txt{display:none}",
                 'nicht erreichbar','beschäftigt','model-banner-switch','model-banner-line'):
   self.assertIn(needle,html)
  self.assertNotIn('Details im Tooltip oben',html)
  self.assertNotIn('Modell nicht verfügbar',html)
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
                 'async function flushStagedFiles(files=[...stagedFiles])',
                 'if(files.length)await flushStagedFiles(files)',
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
  self.request('/api/bot-settings',{'bots':['test'],'thinking':False},method='PUT')
  self.request('/api/message',{'text':'Gespeichert ohne Denken','bots':['test']});self.wait_room('Hallo aus dem Modell.');self.wait_idle()
  self.assertFalse(Model.calls[-1]['chat_template_kwargs']['enable_thinking'])
  self.assertFalse(next(a for a in self.request('/api/state')['agents'] if a['id']=='test')['thinking'])
  # Split tags must never leak reasoning into the answer, even if a backend
  # returns tagged content rather than a separate reasoning channel.
  Model.script=[['<thi','nk>intern','</thi','nk>Nur Antwort.']]
  self.request('/api/message',{'text':'Getrennte Kanäle','bots':['test']});self.wait_room('Nur Antwort.');self.wait_idle()
  self.assertEqual(self.request('/api/state')['room'][-1]['reasoning'],'intern')
  self.request('/api/bot-settings',{'bots':['test'],'thinking':True},method='PUT')
  html=self.request('/',raw=True).decode()
  for needle in ('id="think"',"const bots=[...selected].filter(id=>state.agents.some(a=>a.id===id&&a.enabled!==false)),think=$('think').checked","api('/api/bot-settings'",'a.thinking'):self.assertIn(needle,html)
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
 def test_10c_repeated_autonomy_answer_is_dropped(self):
  # The step streams "Gleich", then the concluding round streams it again.
  # The room and the saved history keep the first answer once.
  command={'tool_calls':[{'index':0,'id':'dup1','function':{'name':'remember','arguments':'{"fact":"dup"}'}}]}
  Model.script=[[command],['Gleich [[MAG:WEITER]]'],['Gleich']]
  try:
   self.request('/api/autotalk',{'on':True})
   self.request('/api/message',{'text':'Wiederhole nicht','bots':['test']})
   self.wait_idle();time.sleep(.4)
   texts=[m.get('content') for m in self.request('/api/state')['room'] if m.get('role')=='assistant']
   self.assertEqual(texts.count('Gleich'),1,texts)
   saved=[m.get('content') for m in json.loads(self.state.joinpath('room.json').read_text()) if m.get('role')=='assistant']
   self.assertEqual(saved.count('Gleich'),1,saved)
  finally:
   self.request('/api/autotalk',{'on':False});Model.script=[];self.wait_idle()
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
 def test_99a_speak_posts_text_voice_message_without_server_tts(self):
  Model.script=[[{'tool_calls':[{'index':0,'id':'call_speak_1','function':{'name':'speak','arguments':'{"text":"Hallo Chris, hier spricht Thorsten."}'}}]}],['Gesagt.']]
  files_before=len(self.request('/api/state').get('files',[]))
  self.request('/api/message',{'text':'Sag Hallo','bots':['test']})
  voice=None
  for _ in range(200):
   voice=next((m for m in self.request('/api/state')['room'] if m.get('kind')=='voice'),None)
   if voice:break
   time.sleep(.05)
  self.wait_idle()
  self.assertIsNotNone(voice,'speak must post a voice message')
  self.assertEqual(voice['content'],'Hallo Chris, hier spricht Thorsten.')
  self.assertEqual(voice['role'],'assistant');self.assertEqual(voice.get('bot'),'test')
  self.assertNotIn('file',voice,'no server-generated audio attachment')
  state=self.request('/api/state')
  self.assertEqual(len(state.get('files',[])),files_before)
  self.assertFalse(any((f.get('name') or '').endswith('.wav') for f in state.get('files',[])))
  api=self.request('/api/speak',{'text':'Direkt gesprochen'})
  self.assertEqual(api['kind'],'voice');self.assertEqual(api['content'],'Direkt gesprochen');self.assertNotIn('file',api)
  self.error(400,'/api/speak',{'text':'   '})
  self.assertFalse(self.espeak_log.exists(),'espeak-ng must never run on the server')
  src=(ROOT/'mag-room/native/magroom.cpp').read_text()
  self.assertNotIn('espeak',src);self.assertNotIn('sprache.wav',src)
  html=self.request('/',raw=True).decode()
  self.assertIn("msg.kind==='voice'",html);self.assertIn('voice-bubble',html)
 def test_99b_room_serves_repo_ui_never_bot_override(self):
  html=self.request('/',raw=True).decode()
  self.assertNotIn('BOT-OVERRIDE',html);self.assertNotIn('message-audio-local.js',html)
  self.assertIn('/static/message-audio.js',html)
  self.assertIn('BOT-OVERRIDE',next(self.state.glob('frontend.html.bak-*')).read_text(),'override kept as timestamped backup')
  self.assertFalse(self.state.joinpath('frontend.html').exists())
  self.error(410,'/api/frontend',{'html':'x'})
  self.error(410,'/api/frontend',method='DELETE')
  # Even a file dropped in at runtime is never served.
  self.state.joinpath('frontend.html').write_text('<html><body>BOT-OVERRIDE'+' '*300+'</body></html>')
  try:self.assertNotIn('BOT-OVERRIDE',self.request('/',raw=True).decode())
  finally:self.state.joinpath('frontend.html').unlink(missing_ok=True)
  src=(ROOT/'mag-room/native/magroom.cpp').read_text()
  self.assertNotIn('"name":"publish_frontend"',src);self.assertNotIn('publish_frontend (alle',src)
  for call in Model.calls:
   names=[t['function']['name'] for t in call.get('tools') or []]
   self.assertNotIn('publish_frontend',names)
 def test_10z_deleted_vm_file_not_republished(self):
  tok=self.state.joinpath('token').read_text().strip();src='/home/bot/room/index.html'
  ingest=lambda body:self.request('/ingest?'+urllib.parse.urlencode({'token':tok,'name':'index.html','bot':'test','src':src}),body,bridge=True,headers={'Content-Type':'application/octet-stream'})
  with self.assertRaises(urllib.error.HTTPError) as ctx:ingest(b'<meta name="mag-token" content="'+_ui_token(self.ui).encode()+b'">')
  self.assertEqual(ctx.exception.code,422,'saved room UI with live token must not be published')
  f=ingest(b'<html>copy</html>');stored=f['stored']
  rec=lambda:next(x for x in json.loads(self.state.joinpath('files.json').read_text()) if x['stored']==stored)
  self.assertEqual((rec()['vm'],rec()['src']),('',src))
  acc=self.state/'access.json';had=acc.read_bytes() if acc.exists() else None
  try:self._access(['chris@github']);gone=self.request('/api/room/files/'+stored,method='DELETE',headers=self._ts('chris@github','Chris'))
  finally:acc.write_bytes(had) if had is not None else acc.unlink(missing_ok=True)
  self.assertTrue(rec()['owner_deleted'] and rec()['removed'],'tombstone keeps vm/src/size')
  self.request('/api/undo/'+gone['undo'],{})
  self.assertNotIn('owner_deleted',rec());self.assertNotIn('removed',rec())
  cpp=(ROOT/'mag-room/native/magroom.cpp').read_text()
  self.assertIn('if (deleted.count(path + "|" + std::to_string(size)))',cpp,'file sync skips chat-deleted unchanged sources')
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
  self.assertIn("'/?bot='+encodeURIComponent(a.id)",html)  # bot menu: "Browser öffnen"
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
  self.assertIn('if(!e.permanent){startHttpLive(gen,id);scheduleReconnect()}',html)
  self.assertIn('browserFallbackError(r.status)',html)
  self.assertIn('HTTP_SCREEN_TIMEOUT_MS=16000',html)
  src=(ROOT/'mag-room/native/magroom.cpp').read_text()
  self.assertIn('browser_start_lock(vm)',src)
  self.assertIn('std::lock_guard<std::mutex> start(*vm_lock)',src)
  self.assertNotIn('std::lock_guard<std::mutex> start(BROWSER_START_MTX)',src)
  self.assertIn('state.agents.some(a=>a.id===old)',html)
  self.assertIn('state.agents.some(a=>a.id===remoteBotId)',html)
  self.assertIn('nv&&liveVmReady&&',html,'do not connect WebRTC until VM exists')
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
  for needle in ('listen 127.0.0.1:4443 ssl http2 proxy_protocol;','auth_request /_mag_whois',
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
  self.assertNotIn('undo',wiped)
  self.assertNotIn('snapshot',wiped)
  self.assertFalse((self.state/'snapshots').exists())
  st=self.request('/api/state')
  self.assertFalse(st['room']);self.assertFalse(st['files'])
  self.assertEqual(self.request('/api/agents/test/memory'),mem)
  self.assertFalse((self.state/'files'/stored).exists())
  st=self.request('/api/state')
  self.assertEqual(st['room'],[])
  self.assertEqual(st['files'],[])
  self.assertNotIn("showUndo('Raum geleert'",self.request('/',raw=True).decode())
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
 def test_17z_hard_clear_owner_only_and_no_undo(self):
  self.wait_idle()
  self._access(['chris@github'])
  owner=self._ts('chris@github','Chris')
  guest=self._ts('ada@github','Ada')
  self.request('/api/agent/test/say',{'text':'Wird gelöscht'})
  record=self.request('/api/upload?name=clear.txt',b'GONE')
  stored=record['stored']
  self.error(403,'/api/room/clear',{},headers=guest)
  self.error(403,'/api/room/clear',{})
  snap=self.state/'snapshots'
  snap.mkdir(exist_ok=True)
  (snap/'old').write_text('legacy')
  result=self.request('/api/room/clear',{},headers=owner)
  self.assertTrue(result['ok'])
  self.assertNotIn('undo',result)
  self.assertNotIn('snapshot',result)
  self.assertFalse(snap.exists())
  self.assertEqual(self.request('/api/state')['room'],[])
  self.assertEqual(self.request('/api/state')['files'],[])
  self.assertEqual(json.loads((self.state/'files.json').read_text()),[])
  self.assertFalse(list((self.state/'files').iterdir()))
  self.error(404,'/api/file/'+stored)
  self.assertTrue(self.request('/api/room/clear',{},headers=owner)['ok'])

 def test_17zz_large_file_and_running_bot_hard_clear(self):
  self.wait_idle()
  self._access(['chris@github'])
  owner=self._ts('chris@github','Chris')
  self.request('/api/room/clear',{},headers=owner)
  rec=self.request('/api/upload?name=large.m4a',b'RECORDING')
  disk=self.state/'files'/rec['stored']
  with disk.open('r+b') as fp:fp.truncate(132_318_913)
  Model.delay=3
  try:
   calls=len(Model.calls)
   self.request('/api/message',{'text':'Old turn must not reply','bots':['test']})
   for _ in range(120):
    if len(Model.calls)>calls:break
    time.sleep(.025)
   self.assertGreater(len(Model.calls),calls,'live model request required')
   self.assertTrue(self.request('/api/state')['busy'])
   done=self.request('/api/room/clear',{},headers=owner)
   self.assertTrue(done['ok'])
   self.assertNotIn('undo',done)
   self.assertNotIn('snapshot',done)
   self.assertFalse((self.state/'snapshots').exists())
   self.assertEqual(json.loads((self.state/'files.json').read_text()),[])
   self.assertFalse(list((self.state/'files').iterdir()))
   time.sleep(.3)
   state=self.request('/api/state')
   self.assertEqual(state['room'],[])
   self.assertEqual(state['files'],[])
   self.assertEqual(state['pending'],[])
   self.assertFalse(state['busy'])
  finally:
   Model.delay=0
   Model.script=[]

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
   st=self.wait_idle()  # the closing bot message lands at the end of the turn
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
  for needle in ('msg-pop','react-pill',"event.type==='reaction'",'/api/messages/'):
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
   st=self.request('/api/state')
   # Bot routine proposals need the user's confirmation card: approve here.
   for a in st.get('approvals') or []:
    if a.get('kind')=='routine':self.request('/api/plugins/approvals/'+a['id'],{'decision':'allow'})
   if not Model.script and not st['busy']:break
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
 def test_17d_human_mcp_create_names_the_owning_bot(self):
  self.wait_idle()
  create=next(t for t in self._mcp('tools/list')['result']['tools'] if t['name']=='jobs_create')
  self.assertIn('bot',create['inputSchema']['properties'])
  self.assertNotIn('bot',create['inputSchema']['required'])
  other=None;made=[]
  try:
   other=self.request('/api/agents',{'name':'Neben','vm':False})
   missing={'name':'Ohne','prompt':'x','schedule':{'kind':'webhook'},'idempotency_key':'human-mcp-missing-bot'}
   bad=self._mcp('tools/call',{'name':'jobs_create','arguments':missing})['result']
   self.assertTrue(bad['isError']);self.assertIn('Bot fehlt',bad['content'][0]['text'])
   named=dict(missing,name='Mit Bot',prompt='y',idempotency_key='human-mcp-with-bot',bot=other['id'])
   ok=self._mcp('tools/call',{'name':'jobs_create','arguments':named})['result']
   self.assertFalse(ok['isError'])
   j=json.loads(ok['content'][0]['text']);made.append(j['id'])
   self.assertEqual(j['bot'],other['id']);self.assertEqual(j['created_by'],'user')
   self._bot_tool_turn('jobs_create',{'name':'Eigener','prompt':'z','schedule':{'kind':'webhook'},'idempotency_key':'bot-cannot-reassign','bot':other['id']})
   own=[x for x in self.request('/api/jobs',method='GET')['jobs'] if x['name']=='Eigener']
   self.assertEqual(len(own),1);self.assertEqual(own[0]['bot'],'test');made.append(own[0]['id'])
   spec=next(t for t in Model.calls[0].get('tools') or [] if t['function']['name']=='jobs_create')
   self.assertNotIn('bot',spec['function']['parameters']['properties'])
  finally:
   Model.script=[]
   if other:
    try:self.request('/api/agents/'+other['id'],method='DELETE')
    except Exception:pass
   for jid in made:
    try:self.request('/api/jobs/'+jid,method='DELETE')
    except Exception:pass


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
  for needle in ("state.single_bot","const addAgent=$('add-agent');if(addAgent)addAgent.hidden=!!single","a.id===single"):self.assertIn(needle,html)
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
 def test_02b_explicit_cross_bot_delegation_uses_target_identity(self):
  # Grok-Bot-style handoff without the discarded groupchat dispatcher:
  # only an explicit tool request launches work in the OTHER bot's lane.
  self.wait_idle()
  call={'tool_calls':[{'index':0,'id':'call_delegate','function':{
   'name':'task','arguments':json.dumps({'action':'delegate','to_bot':'iq3bot',
    'title':'Independent review','prompt':'Review one safe text calculation.'})}}]}
  Model.script=[[call],['Delegation accepted.'],['Review complete.']]
  try:
   self.request('/api/message',{'text':'Delegate a review to IQ3 Bot','bots':['q4bot']})
   rows=[]
   for _ in range(200):
    rows=self.request('/api/tasks')['tasks']
    if any(t.get('requester')=='q4bot' and t.get('bot')=='iq3bot' for t in rows):
     break
    time.sleep(.05)
   else:self.fail('task(delegate) did not create a cross-bot task')
   item=next(t for t in rows if t.get('requester')=='q4bot')
   self.assertEqual(item['bot'],'iq3bot')
   self.assertEqual(item['requester'],'q4bot')
   self.assertTrue(any(t['id']==item['id'] for t in self.request('/api/tasks?bot=q4bot')['tasks']))
   self.assertTrue(any(t['id']==item['id'] for t in self.request('/api/tasks?bot=iq3bot')['tasks']))
   for _ in range(200):
    if next((t['status'] for t in self.request('/api/tasks')['tasks']
             if t['id']==item['id']),None)!='running':break
    time.sleep(.05)
   # A completed child must proactively schedule its original owner on the
   # owner's lane. Merely posting a room message does not satisfy this test.
   for _ in range(200):
    row=next((t for t in self.request('/api/tasks')['tasks'] if t['id']==item['id']),{})
    if row.get('handoff_status')=='ok' and not row.get('handoff_pending'):break
    time.sleep(.05)
   else:self.fail('delegated completion did not wake and finish the owner review')
   self.assertTrue(any('INTERNE EREIGNISAKTIVIERUNG' in
     json.dumps(c.get('messages',[]),ensure_ascii=False) for c in Model.calls),
     'The responsible bot must receive a real model/tool turn after the subagent')
   self.assertEqual(row['requester'],'q4bot')
  finally:
   self.request('/api/stop',{})
   Model.script=[]
 def test_02c_stop_cancels_delegation_without_waking_owner(self):
  self.wait_idle()
  call={'tool_calls':[{'index':0,'id':'call_delegate_stop','function':{
   'name':'task','arguments':json.dumps({'action':'delegate','to_bot':'iq3bot',
    'title':'Stop probe','prompt':'Do long independent read-only work.'})}}]}
  Model.calls=[]
  Model.script=[[call],['Delegated.'],[{'_sleep':6},'Should never finish']]
  try:
   self.request('/api/message',{'text':'Delegate and then stop','bots':['q4bot']})
   for _ in range(100):
    rows=self.request('/api/tasks')['tasks']
    if any(t.get('title','').endswith('Stop probe') and
           t.get('status')=='running' for t in rows):break
    time.sleep(.05)
   else:self.fail('delegation not launched')
   self.request('/api/stop',{})
   self.request('/api/queue/resume',{})
   for _ in range(100):
    rows=self.request('/api/tasks')['tasks']
    ended=[t for t in rows if t.get('title','').endswith('Stop probe')]
    if ended and ended[0].get('status')=='cancelled':break
    time.sleep(.05)
   else:self.fail('delegated work did not cancel')
   self.assertFalse(ended[0].get('handoff_pending'))
   self.assertFalse(any('INTERNE EREIGNISAKTIVIERUNG' in
        json.dumps(c.get('messages',[]),ensure_ascii=False) for c in Model.calls))
  finally:
   self.request('/api/stop',{})
   Model.script=[]
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
 def test_07_configured_deploy_has_no_legacy_multibot_override(self):
  self.assertFalse((ROOT/'ops/mag-120.d/30-multibot.conf').exists())
  self.assertFalse((ROOT/'ops/deepseek/both-sockets.sh').exists())
  drop=(ROOT/'ops/mag-120.d/20-room-audit.conf').read_text()
  self.assertIn('Environment=MAG_ROOM_SINGLE_BOT=\n',drop)
  self.assertIn('EnvironmentFile=-/etc/mag-stack/room-model.env',drop)
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
 def test_10da_stop_never_fakes_idle_or_releases_lane_early(self):
  # Contract against the bug where Stop told UI "idle" while the
  # cancelled worker still held a socket and could race a new turn.
  source=(ROOT/'mag-room/native/magroom.cpp').read_text()
  block=source.split('static Val user_stop(bool pause_jobs) {',1)[1].split(
   '// Step boundary of a running turn',1)[0]
  self.assertNotIn('release_lanes_locked()',block)
  self.assertNotIn('publish_idle_locked()',block)
  self.assertIn('const bool settling = tasks_running() || any_lane_busy_for_stop() || BUSY.load()',block)
  html=(ROOT/'mag-room/web/index.html').read_text()
  self.assertIn('stopAwaitingIdle',html)
  self.assertIn('stopConfirmedIdle()',html)
  self.assertIn('startHttpLive(liveGeneration',html)

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
 def test_10e_global_stop_during_both_prefills_keeps_clean_next_turn(self):
  # Backend sockets are still pre-filling (no output). Stop must interrupt
  # both model lanes, release pending queue, and let a new message start.
  self._idle();Model.delay=5
  try:
   n=len(Model.calls)
   self.request('/api/message',{'text':'Zwei lange Lesevorgaenge','bots':['q4bot','iq3bot']})
   self._calls_after(n,2,1.5)
   queued=self.request('/api/message',{'text':'Verworfen nach Stop','bots':['q4bot']})
   self.assertTrue(queued.get('queued') or self.request('/api/state').get('pending'))
   at=time.monotonic()
   result=self.request('/api/cancel',{})
   self.assertLess(time.monotonic()-at,5,'Stop must not wait for long-running model reads')
   self.assertTrue(result['cancelled'])
   self._idle()
   after=self.request('/api/state')
   self.assertFalse(after['busy'])
   self.assertEqual(after['busy_bots'],[])
   self.assertEqual(after['pending'],[])
   self.assertFalse(after.get('queue_paused',False))
  finally:
   Model.delay=0
  n=len(Model.calls)
  self.request('/api/message',{'text':'Neue Nachricht nach Stop','bots':['q4bot']})
  self._calls_after(n,1,1.5)
  self._idle()
  self.assertEqual(self._last_bot_text('q4bot'),'Hallo aus dem Modell.')


 def test_10f_stop_clears_paused_queue_without_model_activity(self):
  # A paused *existing* queue survives /api/stop by contract. A later
  # composer Stop must clear it even though no model lane is running.
  self._idle()
  Model.delay=5
  try:
   n=len(Model.calls)
   self.request('/api/message',{'text':'Aktiver Lesevorgang','bots':['q4bot']})
   self._calls_after(n,1,1.5)
   self.request('/api/message',{'text':'Wartend nicht mehr ausführen','bots':['q4bot']})
   pre=self.request('/api/state')
   self.assertTrue(pre['pending'],pre)
   self.request('/api/stop',{})
   self._idle()
   before=self.request('/api/state')
   self.assertTrue(before['pending'] and before['queue_paused'],before)
   out=self.request('/api/cancel',{})
   self.assertTrue(out['cancelled'],out)
   self.assertFalse(out.get('settling',False))
   after=self.request('/api/state')
   self.assertEqual(after['pending'],[])
   self.assertFalse(after.get('queue_paused',False))
   self.assertFalse(after['busy'])
  finally:
   Model.delay=0
   self.request('/api/queue',method='DELETE')
   self.request('/api/queue/resume',{})

 def test_10g_stop_new_prefill_within_previous_stop_window(self):
  # Never discard Stop #2 just because the next user starts within 800ms.
  # This was the time-based debounce bug for two quick separate turns.
  self._idle()
  self.request('/api/cancel',{})
  Model.delay=2.5
  try:
   n=len(Model.calls)
   self.request('/api/message',{'text':'Neuer Lesevorgang','bots':['q4bot']})
   self._calls_after(n,1,1.5)
   out=self.request('/api/cancel',{})
   self.assertTrue(out['cancelled'],out)
   self._idle()
   self.assertEqual(self.request('/api/state')['busy_bots'],[])
  finally:
   Model.delay=0


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
  for needle in ("fetch('/api/cancel'",'id="bot-lanes"','id="stop-run"','aria-label="Stoppen"','id="proaktiv"','>Proaktiv<','setBusyBots(next.busy_bots,next.waiting_bots)',"event.type==='idle'&&Array.isArray(event.busy_bots)","event.type==='cancelled'&&event.bot",'state.activities','state.proaktiv_note'):self.assertIn(needle,html)
  for gone in ('Alle stoppen','send-halting','>Stoppen<','siehe Tooltip','Self-Talk','id="selftalk"','id="selftalk-row"'):self.assertNotIn(gone,html)
  # Composer bar: round +, pill with mic, a send button that only sends, and a
  # separate stop icon with no visible text.
  for needle in ('<form id="send-form" class="gbar">','id="plus"','class="composer-card gbar-pill"','id="mic"','id="send-button"','id="stop-run"','id="plus-think"','id="upload-camera"','id="upload-screen"',
                 "function syncStopButton()","$('stop-run').addEventListener('click'",
                 "$('message').addEventListener('input',fitMessageHeight)","syncComposerButton()"):self.assertIn(needle,html)
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
  for needle in ('function showThinking(',"head.className='think-head'","'Gedacht für'","'Denkt nach…'",'msg.reasoning','msg.thought_ms',"event.type==='bot_wait'",'wartet','next.waiting_bots','function thinkSettled(','function paintThinkPeek(',"className='think-dots'",'prefers-reduced-motion:reduce){.think-line.in'):self.assertIn(needle,html)
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
FAKE_GUEST_RUN="""#!/bin/sh
# Fake magbot-guest-run: logs every --stdin script; no guest IP, so the
# browser restart after a workspace reset fails cleanly.
d="$(dirname "$0")"
if [ "$2" = --stdin ]; then { echo "== $1"; cat; echo; } >> "$d/guest.log"; echo reset-ok; fi
exit 0
"""
class BootstrapContext(unittest.TestCase):
 """Fresh and reset bots get the versioned bootstrap context (bot_context.md)."""
 CONTEXT=(ROOT/'mag-room/native/bot_context.md').read_text()
 GERMAN='Deutsch als Standard; sonst Sprache der Anfrage.'
 RULES=('Ergebnis zuerst','prüfe das Ergebnis','Ursache untersuchen',
        'Freigabe vor Geld','Geheimnisse nur über den `secret`-Mechanismus',
        'Keine Rechteausweitung')
 @classmethod
 def setUpClass(cls):
  cls.tmp=tempfile.TemporaryDirectory();tmp=pathlib.Path(cls.tmp.name);cls.state=tmp/'state';cls.state.mkdir()
  cls.ctl=tmp/'ctl';cls.ctl.write_text(MULTIBOT_CTL);cls.ctl.chmod(0o755)
  cls.guest=tmp/'guest-run';cls.guest.write_text(FAKE_GUEST_RUN);cls.guest.chmod(0o755)
  cls.state.joinpath('agents.json').write_text(json.dumps([{'id':'vmbot','name':'VM Bot','vm':'agent9','memory':[]}]))
  cls.model=http.server.ThreadingHTTPServer(('127.0.0.1',0),Model);threading.Thread(target=cls.model.serve_forever,daemon=True).start();cls.ui=port();cls.bridge=port()
  env=dict(os.environ,MAG_OWNER_CANONICAL_LOGIN='chris@github',MAG_TEST_ALLOW_LOCAL_OWNER='1',MAG_ROOM_STATE=str(cls.state),MAG_ROOM_PORT=str(cls.ui),MAG_ROOM_BRIDGE_PORT=str(cls.bridge),MAG_ROOM_BRIDGE_IP='127.0.0.1',MAG_ROOM_BACKEND=f'http://127.0.0.1:{cls.model.server_port}/v1/chat/completions',MAG_ROOM_UI=str(ROOT/'mag-room/web/index.html'),MAG_ROOM_STATIC=str(ROOT/'mag-room/static'),MAG_ROOM_MODEL_STATUS=str(tmp/'ms.json'),MAG_NETMODE=str(NETMODE),MAG_TEST_NO_MEMGIT='1',MAG_ROOM_AGENT_CTL=str(cls.ctl),MAG_ROOM_GUEST_RUN=str(cls.guest),MAG_ROOM_MCP_AUTO='0',MAG_ROOM_STAGGER='off')
  env.pop('MAG_ROOM_SINGLE_BOT',None)
  cls.log=open(tmp/'server.log','a');cls.proc=subprocess.Popen([str(ROOT/'build/magroom')],env=env,stdout=cls.log,stderr=cls.log)
  for _ in range(100):
   try:Native.request.__func__(cls,'/healthz',raw=True);return
   except Exception:time.sleep(.03)
  raise RuntimeError('Native startup failed')
 @classmethod
 def tearDownClass(cls):
  cls.proc.terminate();cls.proc.wait(timeout=5);cls.log.close();cls.model.shutdown();cls.tmp.cleanup()
 request=classmethod(Native.request.__func__)
 wait_idle=Native.wait_idle
 _ask=MultiBot._ask
 def _system(self,bot):
  call=self._ask(bot);sys=call['messages'][0]
  self.assertEqual(sys['role'],'system');return sys['content']
 def test_00_context_source(self):
  self.assertIn(self.GERMAN,self.CONTEXT)
  self.assertIn('Bot-Git: Lesen öffentlicher GitHub-Repos',self.CONTEXT)
  self.assertIn('alleinige Quelle',self.CONTEXT)
  self.assertLess(len(self.CONTEXT),6500,'bootstrap context must stay token-cheap')
  for bad in ('token','password','100.123.','192.168.','stardenburg','publish_frontend','ui_get','frontend_'):
   self.assertNotIn(bad,self.CONTEXT.lower())
  for rule in self.RULES:self.assertIn(rule,self.CONTEXT)
 def test_01_fresh_bot_gets_context(self):
  b=self.request('/api/agents',{'name':'Frisch','vm':False})
  try:
   system=self._system(b['id'])
   self.assertIn(self.CONTEXT,system);self.assertIn(self.GERMAN,system)
   for rule in self.RULES:self.assertIn(rule,system)
   self.assertLess(system.index(self.CONTEXT),system.index('\n[Erinnerungen]'))
  finally:self.request('/api/agents/'+b['id'],method='DELETE')
 def test_02_reset_bot_reprovisioned(self):
  if subprocess.run(['sudo','-n','true'],capture_output=True).returncode!=0:self.skipTest('needs sudo -n for the fake guest runner')
  self.wait_idle()
  try:self.request('/api/agents/vmbot/workspace/reset',{})
  except urllib.error.HTTPError as e:self.assertEqual(e.code,502)  # fake guest has no browser
  log=(self.guest.parent/'guest.log').read_text()
  script=log[log.rindex('== agent9'):]
  self.assertIn('/home/bot/MAG_CONTEXT.md',script);self.assertIn('reset-ok',script)
  self.assertIn(self.GERMAN,script)
  for rule in self.RULES:self.assertIn(rule,script)
  system=self._system('vmbot')
  self.assertIn(self.CONTEXT,system);self.assertIn(self.GERMAN,system)
  for rule in self.RULES:self.assertIn(rule,system)

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
 SECONDARY='qwen'
 """Qwen-only fallback preserves two stable bot identities without DeepSeek."""
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
    MAG_ROOM_SINGLE_BOT='auto',MAG_ROOM_DUAL_NUMA='1',MAG_SECONDARY_MODEL=cls.SECONDARY,MAG_ROOM_PREWARM='0')
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
  self.assertEqual(s['agents'][0]['model'],'qwen3.8-flash-next-uncensored')
  self.assertEqual(s['agents'][1]['model'],'qwen3.8-flash-next-uncensored')
  self.assertEqual([m['id'] for m in s['models']],['qwen3.8-flash-next-uncensored'])
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

ORNITH_CTL="""#!/bin/sh
d="$(dirname "$0")"
echo "$@" >> "$d/ctl.log"
# archive-before-despawn: the room must have written the bot record already
if [ "$1" = despawn ]; then ls "$d/state/archive" > "$d/archive-at-despawn" 2>&1; fi
exit 0
"""
class ConfiguredNuma(unittest.TestCase):
 """Configured DeepSeek mode: two bots with persistent NUMA backend assignments."""
 @classmethod
 def start(cls):
  last=None
  for attempt in range(6):
   if attempt:
    cls.ui=port();cls.bridge=port()
    cls.env['MAG_ROOM_PORT']=str(cls.ui);cls.env['MAG_ROOM_BRIDGE_PORT']=str(cls.bridge)
   cls.log=open(cls.tmp_path/'server.log','a')
   cls.proc=subprocess.Popen([str(ROOT/'build/magroom')],env=cls.env,stdout=cls.log,stderr=cls.log)
   try:wait_http(f'http://127.0.0.1:{cls.ui}/healthz',timeout=8,proc=cls.proc);return
   except Exception as e:
    last=e
    if cls.proc.poll() is None:
     cls.proc.terminate()
     try:cls.proc.wait(timeout=3)
     except Exception:cls.proc.kill();cls.proc.wait(timeout=3)
    cls.log.close()
  raise RuntimeError('Configured NUMA startup failed: %s'%last)
 @classmethod
 def stop(cls):
  cls.proc.terminate();cls.proc.wait(timeout=5);cls.log.close()
 @classmethod
 def setUpClass(cls):
  cls.tmp=tempfile.TemporaryDirectory();cls.tmp_path=pathlib.Path(cls.tmp.name)
  cls.state=cls.tmp_path/'state';cls.state.mkdir()
  cls.ctl=cls.tmp_path/'ctl';cls.ctl.write_text(ORNITH_CTL);cls.ctl.chmod(0o755)
  # Any systemctl call (mag-llm, mag-llm-ornith@N) would land in this log.
  fakebin=cls.tmp_path/'bin';fakebin.mkdir()
  for tool in ('systemctl','virsh'):
   fakebin.joinpath(tool).write_text(f'#!/bin/sh\necho {tool} "$@" >> "{cls.tmp_path}/forbidden.log"\n');fakebin.joinpath(tool).chmod(0o755)
  cls.state.joinpath('agents.json').write_text(json.dumps([
   {'id':'deepseek-chat','name':'DeepSeek 4.0','vm':'agent3','model':'deepseek-v4-flash-0731','memory':[{'text':'bleibt'}]},
   {'id':'lehotbot','name':'Qwen 2','vm':'agent1','model':'deepseek-v4-flash-0731','memory':[]}]))
  cls.model=http.server.ThreadingHTTPServer(('127.0.0.1',0),Model)
  threading.Thread(target=cls.model.serve_forever,daemon=True).start()
  cls.ui=port();cls.bridge=port()
  cls.env=dict(os.environ,PATH=f'{fakebin}:'+os.environ.get('PATH',''),MAG_OWNER_CANONICAL_LOGIN='chris@github',MAG_TEST_ALLOW_LOCAL_OWNER='1',
    MAG_ROOM_STATE=str(cls.state),MAG_ROOM_PORT=str(cls.ui),MAG_ROOM_BRIDGE_PORT=str(cls.bridge),
    MAG_ROOM_BRIDGE_IP='127.0.0.1',MAG_ROOM_BACKEND=f'http://127.0.0.1:{cls.model.server_port}/v1/chat/completions',
    MAG_ROOM_UI=str(ROOT/'mag-room/web/index.html'),MAG_ROOM_STATIC=str(ROOT/'mag-room/static'),
    MAG_ROOM_MODEL_STATUS=str(cls.tmp_path/'ms.json'),MAG_NETMODE=str(NETMODE),
    MAG_TEST_NO_MEMGIT='1',MAG_ROOM_AGENT_CTL=str(cls.ctl),MAG_ROOM_GUEST_RUN='/bin/false',MAG_ROOM_MCP_AUTO='0',
    MAG_ROOM_SINGLE_BOT='auto',MAG_ROOM_SINGLE_BOT_VM='on',MAG_ROOM_DUAL_NUMA='1',MAG_ROOM_MODEL_ID='deepseek-v4-flash-0731',MAG_ROOM_MODEL_LABEL='DeepSeek V4 Flash',MAG_ROOM_MODEL_PORTS='8191:0,8192:1',MAG_ROOM_MAX_BOTS='2',MAG_ROOM_PRESETS=str(ROOT/'ops/room/presets.json'),
    MAG_ROOM_PREWARM='0')  # no MAG_ROOM_STAGGER: Ornith answers concurrently by default
  if getattr(cls,'mixed',False):
   cls.env.update(MAG_ROOM_MODEL_ID_1='qwen3.8-flash-next-uncensored',MAG_ROOM_MODEL_LABEL_1='Qwen3.8 Uncensored')
  cls.start()
 @classmethod
 def tearDownClass(cls):
  cls.stop();cls.model.shutdown();cls.tmp.cleanup()
 request=classmethod(Native.request.__func__)
 error=Native.error
 wait_idle=Native.wait_idle
 def bots(self):return {a['id']:a for a in self.request('/api/state')['agents']}
 def persisted(self):return {a['id']:a for a in json.loads(self.state.joinpath('agents.json').read_text())}
 def ask(self,bot):
  # Model.calls is shared with other classes' rooms; match our own unique text.
  self.wait_idle();token=f'Hallo {bot} {time.monotonic_ns()}'
  self.request('/api/message',{'text':token,'bots':[bot]})
  for _ in range(300):
   hit=[(c,sl) for c,sl in list(Model.pairs) if token in json.dumps(c,ensure_ascii=False)]
   if hit:break
   time.sleep(.02)
  else:self.fail('no model call')
  self.wait_idle();return hit[0]
 def test_01_migration_slots_and_free_mode(self):
  s=self.request('/api/state')
  self.assertTrue(s['configured_model']);self.assertFalse(s['dual_numa']);self.assertEqual(s['single_bot'],'')
  self.assertEqual([(a['id'],a['name'],a['vm'],a['slot'],a['model']) for a in s['agents']],
   [('deepseek-chat','DeepSeek V4 Flash 1','agent3',1,'deepseek-v4-flash-0731'),('lehotbot','DeepSeek V4 Flash 2','agent1',2,'deepseek-v4-flash-0731')])
  self.assertEqual([i['slot'] for i in s['model_instances']],[1,2])
  # Two servers, one per NUMA node, with one fixed llama slot each.
  self.assertEqual([(i['port'],i['numa']) for i in s['model_instances']],[(8191,0),(8192,1)])
  self.assertEqual(self.persisted()['deepseek-chat']['memory'][0]['text'],'bleibt')
 def test_02_german_default_and_sticky_slot(self):
  call,slot=self.ask('lehotbot')
  self.assertEqual(slot,'1','slot 2 -> gateway instance index 1 (port 8192)')
  self.assertIn('Antworte standardmaessig auf Deutsch',call['messages'][0]['content'])
  self.error(409,'/api/agents/lehotbot/model',{'model':'deepseek-v4-flash-0731'})
  self.error(409,'/api/model',{'id':'deepseek-v4-flash-0731'})
 def test_03_two_bot_cap_rejects_third(self):
  with self.assertRaises(urllib.error.HTTPError) as ctx:
   self.request('/api/agents',{'name':'Dritter','vm':False,'preset':'deepseek-v4-dual-numa'})
  self.assertEqual(ctx.exception.code,409)
  self.assertIn('Maximum 2 Bots erreicht',json.loads(ctx.exception.read())['error'])
  self.assertEqual(len(self.bots()),2)
 def test_04_delete_deepseek_chat_archives_and_keeps_other_slots(self):
  before={k:v['slot'] for k,v in self.bots().items()}
  self.assertTrue(self.request('/api/agents/deepseek-chat',method='DELETE')['ok'])
  b=self.bots()
  self.assertNotIn('deepseek-chat',b);self.assertNotIn('deepseek-chat',self.persisted())
  self.assertEqual({k:v['slot'] for k,v in b.items()},{k:v for k,v in before.items() if k!='deepseek-chat'})
  log=self.tmp_path.joinpath('ctl.log').read_text().splitlines()
  self.assertIn('despawn agent3',log)
  self.assertFalse([l for l in log if 'mag-llm' in l or 'systemctl' in l])
  self.assertFalse(self.tmp_path.joinpath('forbidden.log').exists(),'room must never call systemctl/virsh')
  self.assertIn('deepseek-chat-',self.tmp_path.joinpath('archive-at-despawn').read_text())
  arch=list(self.state.joinpath('archive').glob('deepseek-chat-*.json'))
  self.assertEqual(json.loads(arch[0].read_text())['memory'][0]['text'],'bleibt')
 def test_05_readd_takes_lowest_free_slot_and_routes_there(self):
  made=self.request('/api/agents',{'name':'DeepSeek V4 Flash 1','vm':False,'preset':'deepseek-v4-dual-numa'})
  self.assertEqual(self.bots()[made['id']]['slot'],1)
  _,slot=self.ask(made['id'])
  self.assertEqual(slot,'0')
  _,slot=self.ask('lehotbot')
  self.assertEqual(slot,'1','existing bot keeps its instance after delete/re-add')
 def test_06_restart_is_idempotent(self):
  # Startup can sort persisted bots by NUMA slot and finish provisioning a VM.
  # Identity, model routing, memory, and already-owned VMs must stay stable.
  before={a['id']:a for a in json.loads(self.state.joinpath('agents.json').read_text())}
  self.stop();self.start()
  after={a['id']:a for a in json.loads(self.state.joinpath('agents.json').read_text())}
  self.assertEqual(set(after),set(before))
  for bid,original in before.items():
   for field in ('slot','model','memory'):
    self.assertEqual(after[bid].get(field),original.get(field),(bid,field))
   if original.get('vm'):
    self.assertEqual(after[bid].get('vm'),original['vm'],bid)
  self.assertEqual(sorted(a['slot'] for a in self.request('/api/state')['agents']),[1,2])
 def test_07_ui_uses_config_label_and_preset(self):
  html=(ROOT/'mag-room/web/index.html').read_text()
  self.assertIn('state.model_label',html)
  self.assertIn('agent-preset',html)
  self.assertIn('return state.max_bots||2',html)
  self.assertNotIn("c.textContent='Instanz '",html)
  self.assertIn('archiviert, nicht gelöscht',html)
  self.assertIn("'Prompt wird vorbereitet (~'+etaMin(",html)
 def test_08_identity_per_bot_normal_and_deterministic(self):
  bots=self.bots()
  for det in (False,True):
   self.request('/api/model-settings',{'id':'deepseek-v4-flash-0731','deterministic':det})
   try:
    for bid,b in bots.items():
     call,slot=self.ask(bid)
     self.assertEqual(slot,str(b['slot']-1))
     sysmsg=call['messages'][0]
     self.assertEqual(sysmsg['role'],'system')
     self.assertIn('Du bist '+b['name']+':',sysmsg['content'])
     self.assertIn('nicht Claude, nicht Anthropic',sysmsg['content'])
     self.assertIn('Antworte standardmaessig auf Deutsch',sysmsg['content'])
     for other in bots.values():
      if other['id']!=bid:self.assertNotIn('Du bist '+other['name']+',',sysmsg['content'])
     self.assertEqual(call['id_slot'],0)
     self.assertTrue(call['cache_prompt'],'configured bots always keep the slot cache')
     if det:self.assertEqual(call['temperature'],0)
     hit=[p for p in list(Model.paths) if p[3] is call]
     self.assertEqual(hit[0][2],bid,'X-Mag-Bot names the bot')
   finally:
    self.request('/api/model-settings',{'id':'deepseek-v4-flash-0731','deterministic':False})
  # Shared prefix: identical system text up to [Identitaet] for every bot.
  prefixes={c['messages'][0]['content'].split('[Identitaet]')[0] for c in Model.calls[-len(bots)*2:-len(bots)]}
  self.assertEqual(len(prefixes),1)
 def round_msgs(self,token):
  room=self.request('/api/state')['room']
  i=max(k for k,m in enumerate(room) if token in (m.get('content') or ''))
  return [m for m in room[i+1:] if m.get('role')=='assistant']
 def test_10_two_bots_answer_concurrently_order_stable(self):
  bots=self.bots();self.assertEqual(len(bots),2)
  ids=[a['id'] for a in self.request('/api/state')['agents']]
  self.wait_idle();Model.slot_delay={'0':.5,'1':.02,'2':.02,'3':.02}
  gate=threading.Event()
  try:
   token=f'Alle {time.monotonic_ns()}'
   Model.barrier_token=token;Model.barrier_release=gate
   self.request('/api/message',{'text':token,'bots':ids})
   for _ in range(400):
    calls=[sl for c,sl in list(Model.pairs) if token in json.dumps(c,ensure_ascii=False)]
    if len(calls)>=2:break
    time.sleep(.01)
   else:self.fail('not both bots started concurrently')
   self.assertEqual(sorted(calls[:2]),['0','1'])
   self.assertEqual(self.round_msgs(token),[],
    'both started before the fake model releases any response')
   gate.set()
   self.wait_idle()
  finally:
   gate.set();Model.slot_delay={};Model.barrier_token='';Model.barrier_release=None
  got=self.round_msgs(token)
  self.assertEqual(len(got),2)
  self.assertEqual(got[-1]['bot'],ids[0],'slowest bot (slot 1) finished last in the transcript')
  # Stable UI order: one turn group, each answer carries the bot's fixed list position.
  self.assertEqual(len({m['group'] for m in got}),1)
  self.assertEqual(sorted((m['order'],m['bot']) for m in got),[(i,b) for i,b in enumerate(ids)])
  html=(ROOT/'mag-room/web/index.html').read_text()
  self.assertIn('timeline.sort',html)
  self.assertIn('node.dataset.displayMs',html)
  self.assertIn("group:event.group||'',order:+event.order||0",html)
 def test_11_reflection_round_optional_and_cancel(self):
  ids=[a['id'] for a in self.request('/api/state')['agents']]
  self.assertFalse(self.request('/api/state')['reflect'],'reflection round defaults to OFF')
  self.assertTrue(self.request('/api/reflect',{'on':True})['on'])
  try:
   self.assertTrue(self.request('/api/state')['reflect'])
   self.wait_idle();token=f'Runde {time.monotonic_ns()}';mark=len(Model.pairs)
   self.request('/api/message',{'text':token,'bots':ids})
   for _ in range(600):
    got=self.round_msgs(token)
    if len(got)>=2:break
    time.sleep(.02)
   self.wait_idle();got=self.round_msgs(token)
   self.assertEqual(len(got),4,'one reflection answer per bot, never chained')
   first,second=got[:2],got[2:]
   self.assertEqual(len({m['group'] for m in first}),1);self.assertEqual(len({m['group'] for m in second}),1)
   self.assertNotEqual(first[0]['group'],second[0]['group'])
   refl=[(c,sl) for c,sl in list(Model.pairs)[mark:] if 'Reflexionsrunde' in json.dumps(c,ensure_ascii=False)]
   self.assertEqual(sorted(sl for _,sl in refl),['0','1'])
   names={a['id']:a['name'] for a in self.request('/api/state')['agents']}
   for c,sl in refl:
    text=json.dumps(c,ensure_ascii=False)
    for b in ids:self.assertIn(names[b],text,'each bot sees the other first-round answers')
   # Stop one bot during the first round: the others finish, no reflection round.
   self.wait_idle();Model.slot_delay={'1':2.0}
   try:
    token=f'Stopp {time.monotonic_ns()}'
    self.request('/api/message',{'text':token,'bots':ids})
    for _ in range(300):
     if any(token in json.dumps(c,ensure_ascii=False) and sl=='1' for c,sl in list(Model.pairs)):break
     time.sleep(.01)
    time.sleep(.2);self.request('/api/cancel',{'bot':ids[1]})
    self.wait_idle()
   finally:Model.slot_delay={}
   time.sleep(.3);self.wait_idle()
   got=self.round_msgs(token)
   self.assertEqual(sorted(m['bot'] for m in got if not m.get('interrupted')),sorted(b for b in ids if b!=ids[1]))
   self.assertFalse([c for c,_ in list(Model.pairs) if token in json.dumps(c,ensure_ascii=False) and 'Reflexionsrunde' in c['messages'][-1].get('content','')])
  finally:
   self.assertFalse(self.request('/api/reflect',{'on':False})['on'])
 def test_09_prewarm_every_bot_slot_and_share_prefix(self):
  self.wait_idle()
  def warmed(after):
   for _ in range(400):
    got=[p for p in list(Model.paths)[after:] if p[0].startswith('/slots/') and p[3].get('filename','').startswith('mag-prefix-deepseek-v4-flash-0731') and (p[0].endswith('save') or p[0].endswith('restore'))]
    if len({p[1] for p in got})>=2:return list(Model.paths)[after:]
    time.sleep(.03)
   self.fail('not both bot slots prewarmed')
  self.stop()
  Model.saved_prompt.clear()
  meta=self.state/'prompt-prefix.json'
  if meta.exists():meta.write_text('{}')
  type(self).env['MAG_ROOM_PREWARM']='1'
  try:
   mark=len(Model.paths);self.start();seen=warmed(mark)
   saves={(p[1],p[3]['filename']) for p in seen if p[0].endswith('save')}
   self.assertEqual(saves,{(str(n-1),'mag-prefix-deepseek-v4-flash-0731-slot%d.bin'%n) for n in range(1,3)})
   warm=[p for p in seen if p[0]=='/v1/chat/completions' and p[3].get('max_tokens')==1]
   self.assertEqual(sorted(p[1] for p in warm),['0','1'])
   for _ in range(200):  # the room writes the meta file right after each save returns
    meta=json.loads(self.state.joinpath('prompt-prefix.json').read_text() or '{}')
    if len(meta)>=2:break
    time.sleep(.03)
   # Slot files are tracked per server (its own --slot-save-path): slots 1,3 -> 8191, 2,4 -> 8192.
   srv=lambda n:'8191' if n%2 else '8192'
   self.assertEqual(set(meta),{srv(n)+'/mag-prefix-deepseek-v4-flash-0731-slot%d.bin'%n for n in range(1,3)})
   meta={k.split('/',1)[1]:v for k,v in meta.items()}
   # Bots with the same rules (VM vs chat-only) share one prefix key.
   kinds={b['slot']:bool(b['vm']) for b in self.bots().values()}
   self.assertEqual(len(set(meta.values())),len(set(kinds.values())))
   fresh=dict(meta)
   # Stale own files: bots 2..4 restore the prefix bot 1 already warmed.
   self.stop()
   meta={srv(int(k[-5]))+'/'+k:(v if k.endswith('slot1.bin') else 'stale') for k,v in meta.items()}
   self.state.joinpath('prompt-prefix.json').write_text(json.dumps(meta))
   mark=len(Model.paths);self.start();seen=warmed(mark)
   restores={}
   for p in seen:
    if p[0].endswith('restore'):restores.setdefault(p[1],p[3]['filename'])
   own=lambda n:'mag-prefix-deepseek-v4-flash-0731-slot%d.bin'%n
   # Each source holds the same shared prefix; a stale own file is never used
   # when another bot already warmed a matching prefix (slot 1 always does).
   self.assertEqual(sorted(restores),['0','1'])
   for n in range(1,3):self.assertEqual(fresh[restores[str(n-1)]],fresh[own(n)])
   # Never another server's file; bot 3 shares bot 1's server (and its chat-only prefix).
   for n in range(1,3):self.assertEqual(srv(int(restores[str(n-1)][-5])),srv(n),'cross-server restore')
   self.assertEqual(restores,{'0':own(1),'1':own(2)})
   # Save HTTP arrival precedes the room's atomic metadata write.
   for _ in range(200):
    current=json.loads(self.state.joinpath('prompt-prefix.json').read_text() or '{}')
    if all(current.get(srv(n)+'/'+own(n))==fresh[own(n)] for n in range(1,3)):break
    time.sleep(.01)
   else:self.fail('prewarm metadata not committed')
   # Match the pinned ik runtime's actual /slots schema: it has no n_past.
   # Restarting only the room must leave the running model's RAM cache alone.
   self.stop();Model.slot_snapshot=[{'id':0,'state':0,'prompt':'cached-prefix','next_token':{'n_decoded':1}}]
   mark=len(Model.paths);self.start()
   for _ in range(100):
    if 'ram_warm=1' in self.tmp_path.joinpath('server.log').read_text():break
    time.sleep(.03)
   else:self.fail('warm ik slot was not detected')
   self.assertEqual([p for p in list(Model.paths)[mark:] if p[0].startswith('/slots/') or p[0]=='/v1/chat/completions'],[])
  finally:
   self.stop();Model.slot_snapshot=[];type(self).env['MAG_ROOM_PREWARM']='0';self.start()


 def test_zz_fixed_bots_without_vm_get_persistent_workspace_on_restart(self):
  # In configured mode a second bot used to survive as a chat-only shell.
  # A fake agent-control command provisions both; existing IDs/memory persist.
  cls=type(self)
  self.wait_idle()
  cls.stop()
  agents=[
   {'id':'deepseek-chat','name':'DeepSeek','slot':1,'vm':'',
    'model':'deepseek-v4-flash-0731','memory':[{'text':'long-term fact'}]},
   {'id':'qwen-numa1','name':'Qwen','slot':2,'vm':'',
    'model':('qwen3.8-flash-next-uncensored' if getattr(cls,'mixed',False) else 'deepseek-v4-flash-0731'),
    'memory':[{'text':'second memory'}]}]
  self.state.joinpath('agents.json').write_text(json.dumps(agents))
  sudo=self.tmp_path/'bin'/'sudo'
  sudo.write_text('#!/bin/sh\n[ "$1" = "-n" ] && shift\nexec "$@"\n')
  sudo.chmod(0o755)
  cls.env['MAG_ROOM_SINGLE_BOT_VM']='on'
  cls.start()
  try:
   deadline=time.monotonic()+8
   while time.monotonic()<deadline:
    rows=self.request('/api/state')['agents']
    if len(rows)==2 and all(a.get('vm') for a in rows):break
    time.sleep(.05)
   else:self.fail('both configured bots must get VM reservations')
   self.assertEqual({a['vm'] for a in rows},{'agent1','agent2'})
   self.assertEqual({a['id'] for a in rows},{'deepseek-chat','qwen-numa1'})
   self.assertEqual({a['slot'] for a in rows},{1,2})
   saved=self.persisted()
   self.assertEqual(saved['deepseek-chat']['memory'][0]['text'],'long-term fact')
   self.assertEqual(saved['qwen-numa1']['memory'][0]['text'],'second memory')
   self.assertTrue(all('pending_vm' not in a for a in rows))
   logfile=self.tmp_path/'ctl.log'
   spawns=[x for x in logfile.read_text().splitlines() if x.startswith('spawn ')]
   # Both slots have distinct persisted VMs (above). The asynchronous attach
   # can complete across fixture restarts; do not require log append order.
   self.assertTrue(spawns,'Expected VM provisioning')
   self.assertTrue(all(x in ('spawn agent1','spawn agent2') for x in spawns),spawns)
   cls.stop()
   cls.start()
   rows=self.request('/api/state')['agents']
   self.assertEqual({a['vm'] for a in rows},{'agent1','agent2'})
   new_spawns=[x for x in logfile.read_text().splitlines() if x.startswith('spawn ')]
   self.assertEqual(len(new_spawns),len(spawns),'no VM cloned on the next restart')
  finally:
   cls.env.pop('MAG_ROOM_SINGLE_BOT_VM',None)
   sudo.unlink(missing_ok=True)

class MixedNuma(unittest.TestCase):
 mixed=True
 start=classmethod(ConfiguredNuma.start.__func__)
 stop=classmethod(ConfiguredNuma.stop.__func__)
 setUpClass=classmethod(ConfiguredNuma.setUpClass.__func__)
 tearDownClass=classmethod(ConfiguredNuma.tearDownClass.__func__)
 request=classmethod(Native.request.__func__)
 error=Native.error
 wait_idle=Native.wait_idle
 ask=ConfiguredNuma.ask
 bots=ConfiguredNuma.bots
 MIXED_MODELS=('deepseek-v4-flash-0731','qwen3.8-flash-next-uncensored')
 def _prewarm_count(self):
  logp=self.tmp_path.joinpath('server.log')
  if not logp.exists():return 0
  return len([ln for ln in logp.read_text().splitlines() if 'prompt-prewarm' in ln])
 def _prewarm_since(self,seen):
  logp=self.tmp_path.joinpath('server.log')
  if not logp.exists():return []
  return [ln for ln in logp.read_text().splitlines() if 'prompt-prewarm' in ln][seen:]
 def _wait_until(self,pred,timeout=20):
  end=time.monotonic()+timeout
  while time.monotonic()<end:
   if pred():return True
   time.sleep(.02)
  return False
 def _wait_prewarm_models(self,seen,pred,models=None,timeout=20,what='prewarm'):
  models=models or self.MIXED_MODELS
  got={}
  def ready():
   lines=self._prewarm_since(seen)
   found={}
   for m in models:
    hit=[ln for ln in lines if 'model='+m in ln]
    if hit and pred(hit[-1]):found[m]=hit[-1]
   if len(found)==len(models):
    got.clear();got.update(found);return True
   return False
  if self._wait_until(ready,timeout):return got
  log=self.tmp_path.joinpath('server.log').read_text()[-1600:] if self.tmp_path.joinpath('server.log').exists() else ''
  self.fail(what+': '+log)
 def _wait_paths(self,mark,pred,timeout=20,what='paths'):
  found=[]
  def ready():
   got=list(Model.paths)[mark:]
   if pred(got):
    found.clear();found.extend(got);return True
   return False
  if self._wait_until(ready,timeout):return found
  sample=[(p[0],p[1],p[3].get('model') if isinstance(p[3],dict) else None) for p in list(Model.paths)[mark:][-12:]]
  self.fail(what+': '+repr(sample))
 def test_01_per_node_identity_and_preset(self):
  s=self.request('/api/state');bots=s['agents']
  self.assertEqual([(b['slot'],b['model'],b['name']) for b in bots],[(1,'deepseek-v4-flash-0731','DeepSeek V4 Flash'),(2,'qwen3.8-flash-next-uncensored','Qwen3.8 Uncensored')])
  self.assertEqual([m['id'] for m in s['models']],[b['model'] for b in bots])
  self.assertEqual([p['id'] for p in s['presets']],['deepseek-qwen-dual-numa'])
  self.error(409,'/api/agents',{'name':'Dritter','vm':False,'preset':'deepseek-qwen-dual-numa'})
  for b in bots:
   self.assertFalse(b['github_enabled'])
   for thinking in (False,True):
    self.request('/api/bot-settings',{'thinking':thinking,'bots':[b['id']]},method='PUT')
    call,slot=self.ask(b['id'])
    self.assertEqual(slot,str(b['slot']-1));self.assertEqual(call['model'],b['model'])
    self.assertEqual(call['id_slot'],0);self.assertTrue(call['cache_prompt'])
    self.assertEqual(call['chat_template_kwargs']['enable_thinking'],thinking)
    self.assertTrue(any(t['function']['name']=='github' for t in call['tools'])) # schema stable; execution is still gated
  before=self.bots();self.stop();self.start();self.assertEqual(self.bots(),before)
 def test_04_restart_keeps_both_warm_ram_slots(self):
  # RAM is trusted only after both backends saved the current prefix hash.
  # Leftover n_prompt_tokens without that hash is not the room prefix.
  self.wait_idle()
  for bot in self.bots().values():self.ask(bot['id'])
  self.assertTrue(Model.saved_prompt.get('0') and Model.saved_prompt.get('1'))
  self.stop();Model.slot_snapshot=[{'id':0,'n_prompt_tokens':4321}]
  self.env['MAG_ROOM_PREWARM']='1'
  try:
   seen=self._prewarm_count();mark=len(Model.paths);self.start()
   self._wait_prewarm_models(seen,lambda ln:'skipped=ram' in ln,
    what='restart did not skip both warm RAM slots')
   self.assertEqual([p for p in list(Model.paths)[mark:] if p[0].startswith('/slots/') or p[0]=='/v1/chat/completions'],[])
  finally:
   self.stop();Model.slot_snapshot=[];self.env['MAG_ROOM_PREWARM']='0';self.start()
 def _assert_first_hi_both(self,prev):
  for bot in self.bots().values():
   token=f'hi {bot["id"]} {time.monotonic_ns()}'
   t0=time.monotonic()
   self.assertTrue(self._first_hi(bot,token,t0+8),'first hi exceeded the 8s hard timeout for '+bot['model'])
   self.assertLess(time.monotonic()-t0,5,'first hi after a room restart for '+bot['model'])
   rec=[c for c in Model.cache_records if c['model']==bot['model']][-1]
   self.assertGreaterEqual(rec['n_past']/max(prev[bot['id']],1),.98,'first hi must extend the cached prefix')
   self.assertLess(rec['prompt_size']-rec['n_past'],250,'new bytes must be the hi, not a [Kontext] tail')
   call=next(c for c,_ in reversed(list(Model.pairs)) if token in json.dumps(c,ensure_ascii=False))
   self.assertNotIn('[Kontext]',call['messages'][-1]['content'])
   self.assertIn(token,call['messages'][-1]['content'])
   self.assertTrue(call.get('cache_prompt'))
 def test_05_stale_prefix_hash_warms_both_and_first_hi_under_5s(self):
  # Live after #153: ram_warm=1 plus a rewritten hash skipped=ram, so neither
  # bot restored or saved the room prefix. DeepSeek first hi ~17s, Qwen ~48s
  # (tokens_cached=0). A hash change must restore/prewarm both backends.
  self.wait_idle()
  bots=list(self.bots().values())
  prev={}
  Model.last_prompt.clear()
  for bot in bots:
   self.ask(bot['id'])
   prev[bot['id']]=Model.cache_records[-1]['prompt_size']
  self.assertTrue(Model.saved_prompt.get('0') and Model.saved_prompt.get('1'),
                  'both backends must save the slot file')
  self.stop()
  Model.last_prompt.clear()
  meta=self.state/'prompt-prefix.json'
  old=json.loads(meta.read_text()) if meta.exists() else {}
  if not old:
   old={'8191/mag-prefix-deepseek-v4-flash-0731-slot1.bin':'old',
        '8192/mag-prefix-qwen3.8-flash-next-uncensored-slot2.bin':'old'}
  meta.write_text(json.dumps({k:'previous-software-prefix' for k in old}))
  Model.slot_snapshot=[{'id':0,'n_prompt_tokens':1029,'is_processing':False}]
  Model.prefix_miss_s=6
  self.env['MAG_ROOM_PREWARM']='1'
  try:
   seen=self._prewarm_count();mark=len(Model.paths);self.start()
   got=self._wait_prewarm_models(seen,
    lambda ln:'skipped=ram' not in ln and ('skipped=hash' in ln or 'saved=1' in ln or 'restored=1' in ln),
    what='stale hash did not prewarm both slots')
   for ln in got.values():
    self.assertNotIn('skipped=ram',ln,'must not skip leftover RAM when the prefix hash changed')
   self._wait_paths(mark,
    lambda ps:{p[1] for p in ps if p[0]=='/v1/chat/completions' or str(p[0]).endswith(('restore','save'))}>={'0','1'},
    what='stale hash must restore or prewarm both backends')
   gens=[p for p in list(Model.paths)[mark:] if p[0]=='/v1/chat/completions' and (p[3] or {}).get('max_tokens')==1]
   self.assertFalse(gens,'a changed prefix must not start a full prewarm')
   self._assert_first_hi_both(prev)
  finally:
   Model.prefix_miss_s=0;Model.slot_snapshot=[]
   self.env['MAG_ROOM_PREWARM']='0'
   self.stop();self.start()
 def test_07_prefix_stable_across_restart_and_pho(self):
  self.wait_idle()
  bot=next(b for b in self.bots().values() if b['model']=='deepseek-v4-flash-0731')
  first,_=self.ask(bot['id'])
  system,tools=first['messages'][0]['content'],json.dumps(first['tools'],sort_keys=True,ensure_ascii=False)
  self.assertNotIn('\n[Kontext]',system)
  self.request('/api/bot-settings',{'bots':[bot['id']],'pho_compress':not bot.get('pho_compress',True)},method='PUT')
  second,_=self.ask(bot['id'])
  self.assertEqual(second['messages'][0]['content'],system)
  self.assertEqual(json.dumps(second['tools'],sort_keys=True,ensure_ascii=False),tools)
  self.stop();Model.slot_snapshot=[{'id':0,'n_prompt_tokens':4321,'prompt':'cached-prefix'}]
  self.env['MAG_ROOM_PREWARM']='1'
  try:
   seen=self._prewarm_count();mark=len(Model.paths);self.start()
   self._wait_prewarm_models(seen,lambda ln:'skipped=ram' in ln,
    models=('deepseek-v4-flash-0731',),what='prefix-stable restart did not skip warm RAM')
   self.assertFalse([p for p in list(Model.paths)[mark:] if p[0]=='/v1/chat/completions' and p[3].get('max_tokens')==1 and p[3].get('model')=='deepseek-v4-flash-0731'])
   third,_=self.ask(bot['id'])
   self.assertEqual(third['messages'][0]['content'],system)
   self.assertEqual(json.dumps(third['tools'],sort_keys=True,ensure_ascii=False),tools)
   rec=Model.cache_records[-1]
   self.assertGreaterEqual(rec['n_past']/max(rec['prompt_size'],1),.9)
  finally:
   self.stop();Model.slot_snapshot=[];self.env['MAG_ROOM_PREWARM']='0';self.start()
 def test_10_held_prefix_skips_prewarm_and_silent_stream_is_not_resent(self):
  # A held prefix is a live count of the system+tools KV
  # (n_prompt_tokens >= 2048). n_decoded with an empty prompt is not that
  # KV, so this snapshot carries the count. Prewarm must not post.
  # A silent in-progress user completion stays one request.
  self.wait_idle();self.stop()
  Model.slot_snapshot=[{'id':0,'state':0,'prompt':'','is_processing':False,
    'n_prompt_tokens':4321,
    'next_token':{'has_next_token':False,'n_decoded':4,'n_remain':0}}]
  self.env['MAG_ROOM_PREWARM']='1'
  self.env['MAG_ROOM_STREAM_IDLE_S']='1'
  Model.delay=3
  started=time.monotonic()
  try:
   seen=self._prewarm_count();mark=len(Model.paths);self.start()
   self._wait_prewarm_models(seen,lambda ln:'skipped=ram' in ln,
    what='prewarm did not skip a slot whose token count already holds the prefix')
   self.assertFalse([p for p in list(Model.paths)[mark:] if p[0]=='/v1/chat/completions'],
    'resident prefix must not cold-prefill')
   before=len([p for p in Model.paths if p[0]=='/v1/chat/completions'])
   bot=next(iter(self.bots()))
   self.ask(bot)
   calls=[p for p in Model.paths if p[0]=='/v1/chat/completions']
   self.assertEqual(len(calls)-before,1,'silent prefill must not be cancelled and resent')
   self.assertLess(time.monotonic()-started,25,'hard timeout')
  finally:
   Model.delay=0;Model.slot_snapshot=[]
   self.env['MAG_ROOM_PREWARM']='0';self.env.pop('MAG_ROOM_STREAM_IDLE_S',None)
   self.stop();self.start()
 def _first_hi(self,bot,token,deadline):
  self.request('/api/message',{'text':token,'bots':[bot['id']]})
  answered=False
  while time.monotonic()<deadline:
   room=self.request('/api/state')['room']
   for i,m in enumerate(room):
    if m.get('role')=='user' and token in (m.get('content') or ''):
     answered=any(x.get('role')=='assistant' and x.get('bot')==bot['id'] and (x.get('content') or '') for x in room[i+1:])
   if answered:break
   time.sleep(.02)
  return answered
 def test_11_room_restart_first_hi_under_5s(self):
  # Both servers stay up. Only the room process restarts. The first hi is
  # built by magroom and must come back in under 5s for each bot. A ~140
  # token [Kontext] tail or a prefix miss (6s) cannot pass; the poll stops
  # at 8s.
  self.wait_idle()
  bots=list(self.bots().values())
  firsts={};prev={}
  Model.last_prompt.clear()
  for bot in bots:
   firsts[bot['id']],_=self.ask(bot['id'])
   prev[bot['id']]=Model.cache_records[-1]['prompt_size']
   self.assertGreater(prev[bot['id']],200)
  self.assertTrue(Model.saved_prompt.get('0') and Model.saved_prompt.get('1'),
                  'both bots must save the slot file before a restart')
  self.stop()
  Model.slot_snapshot=[{'id':0,'n_ctx':32768,'n_prompt_tokens':12,'speculative':False,'is_processing':False,
    'next_token':{'has_next_token':False,'has_new_line':False,'n_remain':0,'n_decoded':4}}]
  Model.prefix_miss_s=6
  self.env['MAG_ROOM_PREWARM']='1'
  try:
   seen=self._prewarm_count();mark=len(Model.paths);self.start()
   self._wait_prewarm_models(seen,lambda ln:True,what='restart did not settle both slots')
   posted=[p for p in list(Model.paths)[mark:] if p[0]=='/v1/chat/completions']
   self.assertFalse(posted,'room restart posted a completion onto a warm slot')
   self._wait_paths(mark,lambda ps:len([p for p in ps if str(p[0]).endswith('restore')])>=2,
    what='both slot files must be restored after a room restart')
   restored=[p for p in list(Model.paths)[mark:] if str(p[0]).endswith('restore')]
   self.assertGreaterEqual(len(restored),2,'both slot files must be restored after a room restart')
   self.assertTrue(Model.saved_prompt.get('0') and Model.saved_prompt.get('1'),'slot save must write both files')
   for bot in bots:
    token=f'hi {bot["id"]} {time.monotonic_ns()}'
    t0=time.monotonic();deadline=t0+8
    self.assertTrue(self._first_hi(bot,token,deadline),'first hi exceeded the 8s hard timeout for '+bot['model'])
    elapsed=time.monotonic()-t0
    self.assertLess(elapsed,5,'first hi after a room restart for '+bot['model'])
    rec=[c for c in Model.cache_records if c['model']==bot['model']][-1]
    self.assertGreaterEqual(rec['n_past']/prev[bot['id']],.98,'first hi must extend the cached prefix')
    self.assertLess(rec['prompt_size']-rec['n_past'],250,'new bytes must be the hi, not a [Kontext] tail')
    call=next(c for c,_ in reversed(list(Model.pairs)) if token in json.dumps(c,ensure_ascii=False))
    self.assertEqual(call['messages'][:len(firsts[bot['id']]['messages'])],firsts[bot['id']]['messages'])
    self.assertNotIn('[Kontext]',call['messages'][-1]['content'])
    self.assertIn(token,call['messages'][-1]['content'])
    self.assertTrue(call.get('cache_prompt'))
  finally:
   Model.prefix_miss_s=0;Model.slot_snapshot=[]
   self.env['MAG_ROOM_PREWARM']='0'
   self.stop();self.start()
 def test_16_restart_cancels_orphan_and_does_not_reprefill(self):
  # The previous room is gone and the slot is still prefilling (CLOSE-WAIT).
  # The new process cancels that orphan, does not resume it as a 13k
  # completion, restores the prefix once the slot is idle, and answers hi
  # from the cache. SIGTERM itself does not send the cancel.
  self.wait_idle()
  bots=list(self.bots().values())
  qwen=next(b for b in bots if 'qwen' in b['model'])
  Model.last_prompt.clear()
  for bot in bots:self.ask(bot['id'])
  self.assertTrue(Model.saved_prompt.get('0') and Model.saved_prompt.get('1'))
  self.stop()
  (self.state/'queue.json').write_text(json.dumps({
   'jobs':[{'ids':[qwen['id']],'think':-1,'selftalk':True,'input':'bot-to-bot','cause':'m-bot'}],
   'actives':[{'ids':[qwen['id']],'think':-1,'input':'RESUMED-QWEN-JOB'}],
   'pending':[]}))
  (self.state/'deploy-hold').write_text('on\n')
  Model.slot_snapshot=[{'id':0,'is_processing':True,'n_prompt_tokens':0,'n_past':0,'state':0}]
  Model.prefix_miss_s=6
  self.env['MAG_ROOM_PREWARM']='1'
  try:
   mark=len(Model.paths);self.start()
   time.sleep(0.7)
   early=list(Model.paths)[mark:]
   self.assertFalse([p for p in early if p[0]=='/v1/chat/completions'], early)
   self.assertTrue(any('action=cancel' in str(p[0]) for p in early), early)
   log=self.tmp_path.joinpath('server.log').read_text()
   self.assertIn('queue resumed: 1 job(s)', log)
   self.assertIn('orphan completion; cancelling', log)
   self.assertNotIn('not cancelling', log)
   self.assertFalse(self.request('/api/state')['busy'])
   Model.slot_snapshot=[{'id':0,'is_processing':False,'n_prompt_tokens':0,'n_past':0,'state':0,'prompt':''}]
   deadline=time.monotonic()+4
   while time.monotonic()<deadline:
    if any('action=restore' in str(p[0]) for p in list(Model.paths)[mark:]):break
    time.sleep(0.05)
   else:self.fail('idle cold slot was not restored')
   time.sleep(0.35)
   self.assertFalse([p for p in list(Model.paths)[mark:] if p[0]=='/v1/chat/completions'],
                    'resumed job prefilled before the slot was idle and warm')
   for bot in bots:
    token=f'hi {bot["id"]} {time.monotonic_ns()}'
    t0=time.monotonic()
    self.assertTrue(self._first_hi(bot, token, t0+8),'first hi exceeded the 8s hard timeout for '+bot['model'])
    self.assertLess(time.monotonic()-t0, 5, 'first hi after restart for '+bot['model'])
  finally:
   Model.prefix_miss_s=0;Model.slot_snapshot=[]
   self.env['MAG_ROOM_PREWARM']='0'
   for name in ('deploy-hold','proaktiv-resume','selftalk-resume','proaktiv','selftalk'):
    (self.state/name).unlink(missing_ok=True)
   self.stop();self.start()
 def test_12_qwen_answers_while_deepseek_prefills(self):
  # Each bot has its own worker. A long DeepSeek prefill must not delay Qwen.
  self.wait_idle()
  bots=list(self.bots().values())
  ds=next(b for b in bots if 'deepseek' in b['model'])
  qw=next(b for b in bots if 'qwen' in b['model'])
  Model.slot_delay={'0':6}
  try:
   token=f'beide {time.monotonic_ns()}'
   t0=time.monotonic();deadline=t0+8
   self.request('/api/message',{'text':token,'bots':[ds['id'],qw['id']]})
   answered=False
   while time.monotonic()<deadline:
    room=self.request('/api/state')['room']
    for i,m in enumerate(room):
     if m.get('role')=='user' and token in (m.get('content') or ''):
      answered=any(x.get('role')=='assistant' and x.get('bot')==qw['id'] and (x.get('content') or '') for x in room[i+1:])
    if answered:break
    time.sleep(.02)
   elapsed=time.monotonic()-t0
   self.assertTrue(answered,'Qwen exceeded the 8s hard timeout')
   self.assertLess(elapsed,5,'Qwen must answer while DeepSeek is still prefilling')
   calls=[sl for c,sl in list(Model.pairs) if token in json.dumps(c,ensure_ascii=False)]
   self.assertIn('1',calls)
  finally:
   Model.slot_delay={}
   end=time.monotonic()+8
   while time.monotonic()<end:
    if not self.request('/api/state')['busy']:break
    time.sleep(.05)
   else:self.fail('Scheduler failed to become idle')
 def test_13_wiped_slot_restores_file_and_hi_is_under_5s(self):
  # Cancel drops the KV. The room saved it; a restart reloads the file and
  # does not prefill the prompt again.
  self.wait_idle()
  bot=next(b for b in self.bots().values() if b['model']=='deepseek-v4-flash-0731')
  Model.last_prompt.clear();Model.saved_prompt.clear()
  self.ask(bot['id'])
  previous=Model.cache_records[-1]['prompt_size']
  self.assertTrue(Model.saved_prompt.get('0'),'the turn did not save the slot file')
  Model.last_prompt.clear()
  self.stop()
  Model.slot_snapshot=[{'id':0,'n_ctx':32768,'speculative':False,'is_processing':False,
    'next_token':{'has_next_token':False,'has_new_line':False,'n_remain':0,'n_decoded':4}}]
  Model.prefix_miss_s=6
  self.env['MAG_ROOM_PREWARM']='1'
  try:
   mark=len(Model.paths);self.start()
   deadline=time.monotonic()+8
   while time.monotonic()<deadline:
    if any(str(p[0]).endswith('restore') for p in list(Model.paths)[mark:]):break
    time.sleep(.05)
   else:self.fail('wiped slot was not restored')
   self.assertFalse([p for p in list(Model.paths)[mark:] if p[0]=='/v1/chat/completions' and p[1]=='0'])
   token=f'hi {time.monotonic_ns()}'
   t0=time.monotonic();deadline=t0+8
   self.request('/api/message',{'text':token,'bots':[bot['id']]})
   answered=False
   while time.monotonic()<deadline:
    room=self.request('/api/state')['room']
    for i,m in enumerate(room):
     if m.get('role')=='user' and token in (m.get('content') or ''):
      answered=any(x.get('role')=='assistant' and x.get('bot')==bot['id'] and (x.get('content') or '') for x in room[i+1:])
    if answered:break
    time.sleep(.02)
   elapsed=time.monotonic()-t0
   self.assertTrue(answered,'restored hi exceeded the 8s hard timeout')
   self.assertLess(elapsed,5,'first hi after restoring the slot file')
   rec=Model.cache_records[-1]
   self.assertGreaterEqual(rec['n_past']/max(previous,1),.98,'restored prefix must be reused')
  finally:
   Model.prefix_miss_s=0;Model.slot_snapshot=[];Model.saved_prompt.clear()
   self.env['MAG_ROOM_PREWARM']='0'
   self.stop();self.start()
 def test_14_qwen_deterministic_is_prewarmed_and_first_hi_under_5s(self):
  # Production Qwen had deterministic=on, so prewarm skipped it and the
  # first hi sent a cold identity prompt (tokens_cached=0, ~40s).
  self.wait_idle()
  qw=next(b for b in self.bots().values() if 'qwen' in b['model'])
  self.request('/api/model-settings',{'id':qw['model'],'deterministic':True})
  try:
   Model.last_prompt.clear()
   first,_=self.ask(qw['id'])
   self.assertTrue(first.get('cache_prompt'))
   self.assertGreater(len(first['messages'][0]['content']),200)
   previous=Model.cache_records[-1]['prompt_size']
   self.stop()
   Model.slot_snapshot=[{'id':0,'state':0,'prompt':'','is_processing':False,
     'next_token':{'has_next_token':False,'n_decoded':4}}]
   Model.prefix_miss_s=6
   self.env['MAG_ROOM_PREWARM']='1'
   seen=self._prewarm_count();mark=len(Model.paths);self.start()
   self._wait_prewarm_models(seen,lambda ln:True,models=('qwen3.8-flash-next-uncensored',),
    what='Qwen was not prewarmed')
   token=f'hi qwen {time.monotonic_ns()}'
   t0=time.monotonic()
   self.assertTrue(self._first_hi(qw,token,t0+8),'Qwen first hi exceeded the 8s hard timeout')
   self.assertLess(time.monotonic()-t0,5,'Qwen first hi after a room restart')
   rec=[c for c in Model.cache_records if c['model']==qw['model']][-1]
   self.assertGreaterEqual(rec['n_past']/max(previous,1),.98)
   self.assertLess(rec['prompt_size']-rec['n_past'],250)
   call=next(c for c,_ in reversed(list(Model.pairs)) if token in json.dumps(c,ensure_ascii=False))
   self.assertTrue(call.get('cache_prompt'))
   self.assertNotIn('[Kontext]',call['messages'][-1]['content'])
  finally:
   Model.prefix_miss_s=0;Model.slot_snapshot=[]
   self.env['MAG_ROOM_PREWARM']='0'
   self.request('/api/model-settings',{'id':qw['model'],'deterministic':False})
   self.stop();self.start()
 def test_15_leftover_ram_without_hash_prewarms_both_and_first_hi_under_5s(self):
  # Production leftover identity KV reports n_prompt_tokens>0 (ram_warm=1)
  # with no matching prompt-prefix.json. Skipping that leaves Qwen at
  # tokens_cached=0 / ~1029 tokens and DeepSeek at a 17s first hi.
  self.wait_idle()
  bots=list(self.bots().values())
  prev={}
  Model.last_prompt.clear();Model.saved_prompt.clear()
  for bot in bots:
   self.ask(bot['id'])
   prev[bot['id']]=Model.cache_records[-1]['prompt_size']
  self.assertTrue(Model.saved_prompt.get('0') and Model.saved_prompt.get('1'))
  self.stop()
  Model.last_prompt.clear();Model.saved_prompt.clear()
  meta=self.state/'prompt-prefix.json'
  if meta.exists():meta.write_text('{}')
  Model.slot_snapshot=[{'id':0,'n_prompt_tokens':1029,'is_processing':False}]
  Model.prefix_miss_s=6
  self.env['MAG_ROOM_PREWARM']='1'
  try:
   seen=self._prewarm_count();mark=len(Model.paths);self.start()
   got=self._wait_prewarm_models(seen,
    lambda ln:'skipped=ram' not in ln and 'saved=1' in ln,
    what='leftover RAM did not prewarm both slots')
   for ln in got.values():
    self.assertNotIn('skipped=ram',ln,'leftover identity KV is not the room prefix')
    self.assertIn('saved=1',ln)
   self._wait_paths(mark,
    lambda ps:len({p[1] for p in ps if p[0]=='/v1/chat/completions'})>=2 and len([p for p in ps if str(p[0]).endswith('save')])>=2,
    what='both backends must prewarm and save the room prefix')
   self._assert_first_hi_both(prev)
  finally:
   Model.prefix_miss_s=0;Model.slot_snapshot=[]
   self.env['MAG_ROOM_PREWARM']='0'
   self.stop();self.start()
 def test_16_first_hi_thinks_off_and_shows_the_first_token(self):
  # Live: 190 reasoning tokens at 5.9 tok/s before any visible answer, and a
  # 226-token suffix before that. The first user turn disables thinking and
  # publishes the first content token while the rest of the stream is still open.
  self.wait_idle();self.stop()
  self.env.pop('MAG_ROOM_FIRST_HI_THINK',None)
  Model.script=[[{'content':'Hi'},{'_sleep':1.5},{'content':' dort'}]]
  try:
   self.start()
   bot=next(iter(self.bots().values()))
   self.request('/api/bot-settings',{'thinking':True,'bots':[bot['id']]},method='PUT')
   token=f'hi {bot["id"]} {time.monotonic_ns()}'
   t0=time.monotonic()
   self.request('/api/message',{'text':token,'bots':[bot['id']]})
   seen=False
   while time.monotonic()-t0<1.0:
    room=self.request('/api/state')['room']
    if any(m.get('role')=='assistant' and m.get('bot')==bot['id'] and str(m.get('content') or '').startswith('Hi') for m in room):
     seen=True
     break
    time.sleep(.02)
   self.assertTrue(seen,'first token was not in the room within 1s')
   self.assertLess(time.monotonic()-t0,1.0)
   call=next(c for c,_ in reversed(list(Model.pairs)) if token in json.dumps(c,ensure_ascii=False))
   self.assertFalse(call['chat_template_kwargs']['enable_thinking'])
   self.assertNotIn('[Kontext]',call['messages'][-1]['content'])
   self.assertLess(len(call['messages'][-1]['content']),180)
   self.wait_idle()
   call2,_=self.ask(bot['id'])
   self.assertTrue(call2['chat_template_kwargs']['enable_thinking'])
  finally:
   Model.script=[]
   self.env['MAG_ROOM_FIRST_HI_THINK']='on'
   self.stop();self.start()
 def test_18_ready_bot_does_not_take_the_autonomy_owner(self):
  # An existing room, DeepSeek cold and Qwen already resident. The user
  # selected DeepSeek first. Qwen may answer now; it must not become the
  # autonomous owner, and the user line is posted once.
  self.wait_idle()
  self.ask('deepseek-chat')
  self.ask('lehotbot')  # existing secondary ID, now bound to Qwen
  self.stop()
  for name in ('proaktiv','proaktiv-resume','selftalk','selftalk-resume','deploy-hold'):
   (self.state/name).unlink(missing_ok=True)
  meta=self.state/'prompt-prefix.json'
  old_meta=json.loads(meta.read_text()) if meta.exists() else {}
  meta.write_text(json.dumps({k:v for k,v in old_meta.items() if 'qwen' in k}))
  Model.saved_prompt.clear();Model.last_prompt.clear()
  hold=threading.Event()
  Model.slot_block={'0':hold}

  Model.slot_snapshot={
   '0':[{'id':0,'state':0,'prompt':'','is_processing':False,'n_prompt_tokens':12}],
   '1':[{'id':0,'state':0,'prompt':'','is_processing':False,'n_prompt_tokens':4321}]}
  self.env['MAG_ROOM_PREWARM']='1'
  try:
   seen=self._prewarm_count();mark=len(Model.paths);self.start()
   self._wait_prewarm_models(seen,lambda ln:'skipped=ram' in ln,
    models=('qwen3.8-flash-next-uncensored',),
    what='resident Qwen prefix was not left in RAM')
   self._wait_paths(mark,lambda ps:any(p[0]=='/v1/chat/completions' and p[1]=='0' and p[3].get('max_tokens')==1 for p in ps),
    what='cold DeepSeek prewarm did not reach the slot')
   self.assertFalse(self.request('/api/state').get('selftalk'),'proaktiv must stay off so the pending split owns the turn')
   self.request('/api/autotalk',{'on':True})
   command={'tool_calls':[{'index':0,'id':'own1','function':{'name':'remember','arguments':'{"fact":"owner"}'}}]}
   Model.script=[[command],['Qwen bleibt Gast [[MAG:ENDE]]']]
   token=f'Besitzer {time.monotonic_ns()}'
   self.request('/api/message',{'text':token,'bots':['deepseek-chat','lehotbot']})
   def qwen_user(ps):
    return [p for p in ps if p[0]=='/v1/chat/completions' and p[1]=='1' and p[3].get('max_tokens')!=1 and token in json.dumps(p[3],ensure_ascii=False)]
   self._wait_paths(mark,lambda ps:len(qwen_user(ps))>=2,what='Qwen did not finish the split turn')
   time.sleep(.6)
   got=qwen_user(list(Model.paths)[mark:])
   self.assertEqual(len(got),2,[(p[1],p[3].get('max_tokens')) for p in list(Model.paths)[mark:] if p[0]=='/v1/chat/completions'])
   room=self.request('/api/state')['room']
   self.assertEqual(sum(1 for m in room if m.get('role')=='user' and token in (m.get('content') or '')),1)
   jobs=json.loads((self.state/'queue.json').read_text()).get('jobs') or []
   self.assertTrue(any('deepseek-chat' in (j.get('ids') or []) for j in jobs),jobs)
   log=self.tmp_path.joinpath('server.log').read_text()
   self.assertIn('dispatch-owner owner=deepseek-chat ready=lehotbot later=deepseek-chat',log)
   self.assertNotIn('dispatch-owner owner=lehotbot',log)
  finally:
   hold.set()
   Model.slot_block={};Model.slot_snapshot=[];Model.script=[]
   self.env['MAG_ROOM_PREWARM']='0'
   try:self.request('/api/autotalk',{'on':False})
   except Exception:pass
   self.stop();self.start()
 def test_17_restart_does_not_prefill_or_resume_selftalk(self):
  self.wait_idle()
  for bot in self.bots().values():self.ask(bot['id'])
  self.stop()
  (self.state/'selftalk').write_text('on\n')
  (self.state/'selftalk-resume').write_text('on\n')
  (self.state/'deploy-hold').write_text('on\n')
  Model.slot_snapshot=[{'id':0,'n_prompt_tokens':4321,'is_processing':False}]
  self.env['MAG_ROOM_PREWARM']='1'
  try:
   seen=self._prewarm_count();mark=len(Model.paths);self.start()
   self._wait_prewarm_models(seen,lambda ln:'skipped=ram' in ln,
    what='resident prefix was not left untouched')
   time.sleep(0.4)
   self.assertFalse([p for p in list(Model.paths)[mark:] if p[0]=='/v1/chat/completions'],
    'restart posted a completion or resumed proactive work')
   self.assertTrue((self.state/'deploy-hold').is_file())
  finally:
   Model.slot_snapshot=[]
   self.env['MAG_ROOM_PREWARM']='0'
   for name in ('proaktiv','proaktiv-resume','selftalk','selftalk-resume','deploy-hold'):
    (self.state/name).unlink(missing_ok=True)
   self.stop();self.start()
 def test_08_health_timeout_is_busy_not_offline(self):
  self.wait_idle();self.stop();Model.hold_get=6;self.env['MAG_ROOM_PREWARM']='1'
  try:
   mark=len(Model.paths);self.start()
   st=self.request('/api/model-status')
   self.assertEqual(st['state'],'busy');self.assertTrue(st['ready'])
   self.assertNotIn('nicht erreichbar',st['label']);self.assertIn('rechnet',st['label'])
   deadline=time.monotonic()+3
   while time.monotonic()<deadline and not [p for p in list(Model.paths)[mark:] if p[0]=='/v1/chat/completions']:
    time.sleep(.05)
   self.assertFalse([p for p in list(Model.paths)[mark:] if p[0]=='/v1/chat/completions'])
  finally:
   Model.hold_get=0;self.stop();self.env['MAG_ROOM_PREWARM']='0';self.start()
 def test_03_second_request_uses_positive_n_past_for_both_models(self):
  for b in self.bots().values():
   self.wait_idle();Model.last_prompt.clear();mark=len(Model.cache_records)
   first,_=self.ask(b['id']);second,_=self.ask(b['id'])
   self.assertEqual(second['messages'][:len(first['messages'])],first['messages'],'previous wire messages stay byte-identical')
   calls=[c for c in Model.cache_records[mark:] if c['model']==b['model']]
   self.assertEqual(len(calls),2)
   self.assertEqual(calls[0]['n_past'],0)
   self.assertGreaterEqual(calls[1]['n_past']/calls[0]['prompt_size'],.95,'second request must reuse >=95% of the prior prefix')
   self.assertTrue(all(c['slot']==0 and c['cache_prompt'] for c in calls))
 def test_06_switches_keep_prefix_and_tool_schema(self):
  for b in self.bots().values():
   self.wait_idle();first,_=self.ask(b['id']);previous=Model.cache_records[-1]['prompt_size'];mark=len(Model.cache_records)
   self.request('/api/bot-settings',{'bots':[b['id']],'github_enabled':True,'thinking':True},method='PUT')
   try:
    second,_=self.ask(b['id'])
    self.assertEqual(first['tools'],second['tools'])
    self.assertEqual(second['messages'][:len(first['messages'])],first['messages'])
    record=Model.cache_records[mark]
    self.assertGreaterEqual(record['n_past']/previous,.95)
    self.assertNotIn('[Kontext]',second['messages'][-1]['content'],
                     'a later turn must not re-send the ~140-token [Kontext] tail')
   finally:self.request('/api/bot-settings',{'bots':[b['id']],'github_enabled':False,'thinking':False},method='PUT')
 def test_02_prewarm_isolated_per_model(self):
  self.wait_idle();self.stop()
  Model.saved_prompt.clear()
  meta=self.state/'prompt-prefix.json'
  if meta.exists():meta.write_text('{}')
  self.env['MAG_ROOM_PREWARM']='1'
  try:
   mark=len(Model.paths);self.start()
   for _ in range(400):
    saves=[p for p in list(Model.paths)[mark:] if p[0].endswith('save')]
    if len(saves)>=2:break
    time.sleep(.03)
   self.assertEqual({(p[1],p[3]['filename']) for p in saves},{('0','mag-prefix-deepseek-v4-flash-0731-slot1.bin'),('1','mag-prefix-qwen3.8-flash-next-uncensored-slot2.bin')})
   warm=[p for p in list(Model.paths)[mark:] if p[0]=='/v1/chat/completions' and p[3].get('max_tokens')==1]
   self.assertEqual({(p[1],p[3]['model']) for p in warm},{('0','deepseek-v4-flash-0731'),('1','qwen3.8-flash-next-uncensored')})
   for p in warm:self.assertEqual(p[3]['id_slot'],0);self.assertTrue(p[3]['cache_prompt'])
  finally:
   self.stop();self.env['MAG_ROOM_PREWARM']='0';self.start()

class PhoCompress(unittest.TestCase):
 mixed=True
 start=classmethod(ConfiguredNuma.start.__func__)
 stop=classmethod(ConfiguredNuma.stop.__func__)
 def setUp(self):
  # Each case gets its own room, wire archive and backend. Restart tests may
  # retain state inside a case, never from a preceding case.
  Model.script=[];Model.script_scope=None;Model.reply=None;Model.delay=0
  Model.tokenizer_fail=False
  ConfiguredNuma.setUpClass.__func__(type(self))
  self.addCleanup(ConfiguredNuma.tearDownClass.__func__,type(self))
  self.addCleanup(self.model.server_close)
 request=classmethod(Native.request.__func__)
 error=Native.error
 wait_idle=Native.wait_idle
 bots=ConfiguredNuma.bots
 PROSE='Heute hat der Bundestag den Entwurf zur Datenspeicherung beschlossen. Kritiker warnen vor einem Eingriff in die Privatsphäre. Bitte die wichtige Frage zur Architektur und zur Kompression der Sprache beantworten. '
 def send(self,bot,text):
  self.wait_idle();mark=len(Model.calls)
  self.request('/api/message',{'text':text,'bots':[bot]});self.wait_idle()
  calls=[c for c in Model.calls[mark:] if c.get('model')==self.bots()[bot]['model'] and c.get('max_tokens')!=1]
  self.assertTrue(calls);return calls[0]
 def wire(self,model):
  files=[json.loads(p.read_text()) for p in self.state.glob('prompt-wire-*.json')]
  return next(w for w in files if model in w['messages'][0]['content'])
 def assert_warm_prefix(self,previous,call,record):
  # A warm request resends every previous message unchanged. Compression must
  # not rewrite history the prompt cache still reuses.
  self.assertEqual(call['messages'][:len(previous['messages'])],previous['messages'])
  self.assertEqual(call.get('tools'),previous.get('tools'))
  self.assertGreater(record['n_past'],0)
 def test_01_default_switch_and_room_persistence(self):
  self.assertTrue(self.request('/api/state')['pho_compress_default'])
  self.assertTrue(all(b['pho_compress'] for b in self.bots().values()))
  bot=next(iter(self.bots()))
  self.request('/api/bot-settings',{'bots':[bot],'pho_compress':False},method='PUT')
  self.stop();self.start();self.assertFalse(self.bots()[bot]['pho_compress'])
  self.assertTrue(any(b['pho_compress'] for b in self.bots().values()))
  self.request('/api/bot-settings',{'scope':'room','pho_compress':False},method='PUT')
  self.stop();self.start();self.assertFalse(self.request('/api/state')['pho_compress_default'])
  self.assertTrue(all(not b['pho_compress'] for b in self.bots().values()))
  self.error(400,'/api/bot-settings',{'scope':'room','pho_compress':'yes'},method='PUT')
  self.request('/api/bot-settings',{'scope':'room','pho_compress':True},method='PUT')
 def test_02_each_model_archives_once_without_rewriting_warm_prefix(self):
  for b in self.bots().values():
   previous=None;archive={}
   for turn in range(7):
    mark=len(Model.cache_records)
    call=self.send(b['id'],self.PROSE+' Zahlen 001234 -1.23e-4 URL https://example.test/a?x=12 ID user_id-ABC_123. Turn '+str(turn))
    if previous:
     self.assert_warm_prefix(previous,call,Model.cache_records[mark])
    self.assertEqual(call['id_slot'],0);self.assertTrue(call['cache_prompt'])
    previous=call
    w=self.wire(b['model'])
    for key,value in archive.items():self.assertEqual(w['pho_archive'][key],value,'a sealed turn must never be recompressed')
    archive=w['pho_archive']
   accepted=[r for r in archive.values() if r['content']!=r['original']]
   self.assertTrue(accepted,'older ordinary prose must be archived compactly for '+b['model'])
   for r in accepted:
    self.assertLess(r['tokens_after'],r['tokens_before'])
    self.assertNotIn('[PhO-Compress]',r['content'])
    if '001234' in r['original']:
     for literal in ('001234','-1.23e-4','https://example.test/a?x=12','user_id-ABC_123'):
      self.assertIn(literal,r['content'])
   self.assertFalse(any('[PhO-Compress]' in m.get('content','') for m in previous['messages']))
   # messages[1] is the first transcript. It may seal room history that this
   # bot had not sent yet. Every later message was sent raw and must stay raw.
   for m in previous['messages'][2:]:
    self.assertNotIn('today bund', m.get('content') or '')
    self.assertNotIn('aus model .', m.get('content') or '')
   self.assertTrue(any('today bund' in r['content'] and 'Bundestag' in r['original'] for r in accepted),
                   'aged prose is sealed for rollover without rewriting the warm wire')
   self.assertFalse(any('aus model .' in (m.get('content') or '') for m in previous['messages']))
   users=[m for m in previous['messages'] if m.get('role')=='user']
   self.assertIn('Bundestag',users[-1]['content'])
   self.assertIn('Bundestag',users[-2]['content'])
   room=self.request('/api/state')['room']
   self.assertTrue(any('Bundestag' in (m.get('content') or '') for m in room))
   self.assertFalse(any('today bund' in (m.get('content') or '') for m in room))
   self.stop();self.start()
   mark=len(Model.cache_records)
   resumed=self.send(b['id'],self.PROSE+' Wiederaufnahme')
   self.assert_warm_prefix(previous,resumed,Model.cache_records[mark])
   after=self.wire(b['model'])['pho_archive']
   for key,value in archive.items():self.assertEqual(after[key],value)
  log=self.tmp_path.joinpath('server.log').read_text()
  for model in ('deepseek-v4-flash-0731','qwen3.8-flash-next-uncensored'):
   self.assertIn('pho-compress scope=history model='+model+' tokens_before=',log)
  self.assertNotIn('scope=live',log,'live compression stays off unless MAG_ROOM_PHO_LIVE=1')
 def test_03b_missing_tokenizer_keeps_original_and_warm_slot(self):
  b=next(iter(self.bots().values()));previous=None;Model.tokenizer_fail=True
  marker='TokenizerFallback'+str(time.monotonic_ns())
  try:
   for n in range(6):
    call=self.send(b['id'],self.PROSE+marker+' '+str(n))
    if previous:self.assertEqual(call['messages'][:len(previous['messages'])],previous['messages'])
    previous=call
   records=[r for r in self.wire(b['model'])['pho_archive'].values() if marker in r['original']]
   self.assertTrue(records)
   for r in records:
    self.assertEqual(r['content'],r['original']);self.assertEqual(r['tokens_before'],-1)
  finally:Model.tokenizer_fail=False
 def test_04_rollover_uses_archive_and_keeps_recent_turns(self):
  for b in self.bots().values():
   code='```cpp\nconst char *URL = "https://github.com/o/r"; int id = 001234;\n```'
   self.send(b['id'],'Bitte Code exakt aufbewahren: '+code)
   previous=None;rolled=None
   for n in range(32):
    call=self.send(b['id'],self.PROSE*13+' Lange Historie '+str(n))
    if any('today bund' in m.get('content','') for m in call['messages'][2:]):
     rolled=call
     if previous:
      recent=[m for m in previous['messages'] if m.get('role')=='user'][-2:]
      for m in recent:self.assertIn(m,call['messages'],'last two complete turns stay exact')
     break
    previous=call
   self.assertIsNotNone(rolled,'regular context rollover must consume stored compression for '+b['model'])
   # Rollover already rebuilt this copy. The next request must resend it
   # unchanged: a warm turn never rewrites a message the previous call sent.
   mark=len(Model.cache_records)
   warm=self.send(b['id'],'Kurze Anschlussfrage nach dem Kontextwechsel.')
   self.assert_warm_prefix(rolled,warm,Model.cache_records[mark])
   self.assertFalse(any(r['content']!=r['original'] for r in self.wire(b['model'])['pho_archive'].values() if code in r['original']))
 def test_03_plus_menu_label_and_order(self):
  html=(ROOT/'mag-room/web/index.html').read_text()
  self.assertLess(html.index('id="plus-github-access"'),html.index('id="plus-think"'))
  self.assertLess(html.index('id="plus-think"'),html.index('id="plus-pho"'))
  self.assertIn('<span>PhO-Compress</span>',html)

 def test_05_initial_window_keeps_two_completed_turns_and_skips_evicted_text(self):
  self.wait_idle();self.stop()
  room=[{'id':'pho-evicted','role':'user','name':'Du','content':self.PROSE*200+' NOT_IN_WINDOW'},
        {'id':'pho-evicted-answer','role':'assistant','name':'Bot','content':'Alte Antwort.'}]
  for i in range(80):
   room += [{'id':'pho-old-'+str(i),'role':'user','name':'Du','content':self.PROSE+' Alt '+str(i)},
            {'id':'pho-answer-'+str(i),'role':'assistant','name':'Bot','content':'Abgeschlossen.'}]
  recent=[self.PROSE+' RECENT_A',self.PROSE+' RECENT_B']
  for i,text in enumerate(recent):
   room += [{'id':'pho-recent-'+str(i),'role':'user','name':'Du','content':text},
            {'id':'pho-done-'+str(i),'role':'assistant','name':'Bot','content':'Antwort bleibt lesbar.'}]
  self.state.joinpath('room.json').write_text(json.dumps(room))
  for p in self.state.glob('prompt-wire-*.json'):p.unlink()
  self.start();mark=len(Model.tokenizer_requests)
  b=next(iter(self.bots().values()));call=self.send(b['id'],'Neue noch nicht beantwortete Anfrage.')
  text=call['messages'][1]['content']
  for original in recent:self.assertIn(original,text,'two completed exchanges plus pending request stay raw')
  self.assertFalse(any('NOT_IN_WINDOW' in t for t in Model.tokenizer_requests[mark:]),'evicted history must not invoke the tokenizer')
 def test_06_tool_round_compaction_invalidates_old_offsets_even_when_history_grows(self):
  b=next(iter(self.bots().values()));self.send(b['id'],'Neue Ausgangslage.');self.stop()
  path=next(p for p in self.state.glob('prompt-wire-*.json') if b['model'] in json.loads(p.read_text())['messages'][0]['content'])
  w=json.loads(path.read_text());w['messages']=w['messages'][:2]
  w['messages'][1]['content']='Initial context stays exact.'
  w['messages'] += [{'role':'assistant','content':'```'+('x'*30000)+'```'},
                    {'role':'user','content':'```'+('y'*8000)+'```'},
                    {'role':'assistant','content':'```'+('z'*8000)+'```'}]
  w['turn_ends']=[3,5];path.write_text(json.dumps(w));self.start()
  Model.script=[[{'tool_calls':[{'index':0,'id':'pho-files','function':{'name':'list_room_files','arguments':'{}'}}]}],['Die Dateien sind geprüft.']]
  try:self.send(b['id'],'Dateien nach Kontextwechsel prüfen.')
  finally:Model.script=[]
  result=self.wire(b['model'])
  self.assertGreaterEqual(len(result['messages']),5,'tool rounds grew the vector back beyond its old size')
  self.assertEqual(result['turn_ends'],[len(result['messages'])],'erased-message offsets must be forgotten immediately')
 def test_07_ui_savings_are_measured_tokens_and_omitted_when_none(self):
  b=next(iter(self.bots().values()))
  self.request('/api/bot-settings',{'bots':[b['id']],'pho_compress':False},method='PUT')
  self.send(b['id'],self.PROSE+' aus')
  self.assertNotIn('pho_save',self.bots()[b['id']])
  self.request('/api/bot-settings',{'bots':[b['id']],'pho_compress':True},method='PUT')
  for n in range(6):self.send(b['id'],self.PROSE+' Ersparnis '+str(n))
  save=self.bots()[b['id']]['pho_save']
  self.assertGreater(save['session_before'],save['session_after'])
  self.assertGreater(save['session_before'],0)
  wire=self.wire(b['model']);rec=wire['pho_archive'][wire['pho_last_key']]
  self.assertEqual(save['last_before'],rec['tokens_before'])
  self.assertEqual(save['last_after'],rec['tokens_after'])
  self.assertGreater(save['last_before'],save['last_after'])
  gains=[m for m in self.request('/api/state')['room'] if m.get('pho_before',0)>m.get('pho_after',0)]
  self.assertTrue(gains,'accepted compression is attached to the original message')
  self.assertEqual(gains[-1]['pho_before'],rec['tokens_before'])
  self.assertEqual(gains[-1]['pho_after'],rec['tokens_after'])
  html=(ROOT/'mag-room/web/index.html').read_text()
  self.assertIn('function phoGainText(',html)
  self.assertIn("return phoK(before)+' → '+phoK(after)+' Tokens (−'+pct+'%)'",html)
  self.assertIn("className='pho-gain'",html)
  self.assertIn("return pct==null?'':'PhO −'+pct+' %'",html)
  self.assertIn('function paintRoomLine(',html)
  self.assertIn('function roomClearedNow(',html)
  self.assertIn("event.type==='room_cleared'",html)
  self.assertNotIn('async function roomRetry(',html)
  self.assertIn('Kein automatischer zweiter Löschversuch',html)
 def test_08_aged_history_is_compressed_and_the_prefix_stays_cacheable(self):
  b=next(iter(self.bots().values()))
  code='```cpp\nconst char *URL = "https://github.com/o/r"; int id = 001234;\n```'
  first=self.send(b['id'],self.PROSE+' Turn A')
  mark=len(Model.cache_records)
  second=self.send(b['id'],'Bitte Code exakt aufbewahren: '+code)
  # A second turn must not re-prefill the system prompt or the first transcript.
  self.assertEqual(second['messages'][0],first['messages'][0])
  self.assertEqual(second['messages'][1],first['messages'][1])
  self.assertEqual(second.get('tools'),first.get('tools'))
  self.assertGreater(Model.cache_records[mark]['n_past'],len(first['messages'][0]['content']))
  call_mark=len(Model.calls)
  Model.script=[[{'tool_calls':[{'index':0,'id':'pho-hist','function':{'name':'list_room_files','arguments':'{}'}}]}],['Werkzeug ist fertig.']]
  try:self.send(b['id'],self.PROSE+' Turn tool')
  finally:Model.script=[]
  tool_msgs=[m for c in Model.calls[call_mark:] for m in c.get('messages',[]) if m.get('role')=='tool']
  self.assertTrue(tool_msgs,'this send must produce its own tool result')
  tool_text=tool_msgs[-1].get('content')
  self.assertTrue(tool_text)
  self.assertNotIn('webhook_path',tool_text)
  previous=None
  for n in range(6):
   mark=len(Model.cache_records)
   call=self.send(b['id'],self.PROSE+' Verlauf '+str(n))
   if previous:self.assert_warm_prefix(previous,call,Model.cache_records[mark])
   previous=call
  texts=[m.get('content','') for m in previous['messages']]
  joined='\n'.join(texts)
  self.assertIn(code,joined)
  self.assertIn(tool_text,joined)
  self.assertIn('Hallo aus dem Modell.',joined)
  self.assertNotIn('aus model .',joined)
  self.assertIn('Bundestag',joined)
  self.assertNotIn('today bund',joined)
  self.assertTrue(any(m.get('role')=='tool' and m.get('content')==tool_text for m in previous['messages']))
  users=[m for m in previous['messages'] if m.get('role')=='user']
  self.assertIn('Verlauf 5',users[-1]['content'])
  self.assertIn('Bundestag',users[-1]['content'])
  self.assertNotIn('today bund',users[-1]['content'])
  self.assertIn('Bundestag',users[-2]['content'])
  room=self.request('/api/state')['room']
  self.assertTrue(any('Verlauf 5' in (m.get('content') or '') and 'Bundestag' in (m.get('content') or '') for m in room))
  self.assertFalse(any('today bund' in (m.get('content') or '') for m in room))
  archive=self.wire(b['model'])['pho_archive']
  accepted=[r for r in archive.values() if r['content']!=r['original']]
  self.assertTrue(any('today bund' in r['content'] and 'Bundestag' in r['original'] and r['tokens_after']<r['tokens_before'] for r in accepted))
  for r in archive.values():
   if 'Hallo aus dem Modell' in r['original']:self.assertEqual(r['content'],r['original'])
   if code in r['original']:self.assertEqual(r['content'],r['original'])
  mark=len(Model.cache_records)
  again=self.send(b['id'],self.PROSE+' Verlauf nochmal')
  self.assert_warm_prefix(previous,again,Model.cache_records[mark])
  self.assertEqual(again['messages'][0]['content'],previous['messages'][0]['content'])
  saves=[]
  for line in self.tmp_path.joinpath('server.log').read_text().splitlines():
   if 'scope=history' in line and 'saved=' in line:
    saves.append(int(line.split('saved=',1)[1].split()[0]))
  self.assertTrue(any(s>0 for s in saves),'aged history must log a positive token saving')

 def test_09_live_compacts_new_prose_once_and_keeps_the_prefix(self):
  # Single-line prose the byte tokenizer accepts (>=15% and >=4). Quotes,
  # code and a line break must stay verbatim. The room record stays original.
  news=('Heute hat der Bundestag den Entwurf zur Datenspeicherung beschlossen. '
        'Kritiker warnen vor einem Eingriff in die Privatsphäre. Bitte die wichtige '
        'Frage zur Architektur und zur Kompression der Sprache beantworten.')
  other_prose=('Bitte prüfe die bestehende Änderung der Arbeit und gib eine klare Antwort '
               'mit einem Beispiel für die Bedeutung der Aufgabe. Der Nutzer soll die Anfrage '
               'behalten und die Aufmerksamkeit auf den Anfang der Änderung legen. Die Frage '
               'zur Architektur der Kompression bleibt wichtig und die Sprache soll verständlich bleiben.')
  self.stop();type(self).env['MAG_ROOM_PHO_LIVE']='1';self.start()
  ids=list(self.bots());b=ids[0];other=ids[1]
  mark_tok=len(Model.tokenizer_requests)
  first=self.send(b,news)
  self.assertEqual(len(Model.tokenizer_requests)-mark_tok,2,'an accepted live line tokenizes original and candidate once')
  users=[m for m in first['messages'] if m.get('role')=='user']
  self.assertIn('today bund',users[-1]['content'])
  self.assertNotIn('Bundestag',users[-1]['content'])
  room=self.request('/api/state')['room']
  self.assertTrue(any(news in (m.get('content') or '') for m in room))
  self.assertFalse(any('today bund' in (m.get('content') or '') for m in room))
  self.stop()
  stored=json.loads(self.state.joinpath('room.json').read_text())
  stored.append({'id':'pho-live-other','role':'assistant','name':'Andere','bot':other,
                 'content':other_prose,'ts':1,'display_ms':1})
  self.state.joinpath('room.json').write_text(json.dumps(stored))
  self.start()
  mark=len(Model.cache_records);mark_tok=len(Model.tokenizer_requests)
  second=self.send(b,'Hallo')
  self.assertGreaterEqual(len(Model.tokenizer_requests)-mark_tok,2,'the other bot line is tokenized when it first enters the wire')
  self.assert_warm_prefix(first,second,Model.cache_records[mark])
  latest=[m for m in second['messages'] if m.get('role')=='user'][-1]['content']
  self.assertIn('Hallo',latest)
  self.assertIn('attention',latest)
  self.assertNotIn('Aufmerksamkeit',latest)
  self.assertNotIn(other_prose,latest)
  room=self.request('/api/state')['room']
  self.assertTrue(any(other_prose in (m.get('content') or '') for m in room))
  mark=len(Model.cache_records);mark_tok=len(Model.tokenizer_requests)
  code='```cpp\nconst char *URL = "https://github.com/o/r"; int id = 001234;\n```'
  coded=self.send(b,'Bitte Code exakt aufbewahren: '+code)
  self.assertEqual(len(Model.tokenizer_requests)-mark_tok,0,'code must not call the tokenizer')
  self.assert_warm_prefix(second,coded,Model.cache_records[mark])
  self.assertIn(code,[m for m in coded['messages'] if m.get('role')=='user'][-1]['content'])
  mark=len(Model.cache_records);mark_tok=len(Model.tokenizer_requests)
  quoted='"Zitat" '+news
  q=self.send(b,quoted)
  self.assertEqual(len(Model.tokenizer_requests)-mark_tok,0,'quoted prose must not call the tokenizer')
  self.assert_warm_prefix(coded,q,Model.cache_records[mark])
  self.assertIn('Bundestag',[m for m in q['messages'] if m.get('role')=='user'][-1]['content'])
  mark=len(Model.cache_records)
  multi=news+'\n'+news
  m=self.send(b,multi)
  self.assert_warm_prefix(q,m,Model.cache_records[mark])
  self.assertIn(news,[m for m in m['messages'] if m.get('role')=='user'][-1]['content'])
  Model.tokenizer_fail=True
  try:
   mark=len(Model.cache_records)
   failed=self.send(b,news+' Tokenizer aus.')
   self.assert_warm_prefix(m,failed,Model.cache_records[mark])
   self.assertIn('Bundestag',[m for m in failed['messages'] if m.get('role')=='user'][-1]['content'])
  finally:Model.tokenizer_fail=False
  self.request('/api/bot-settings',{'bots':[b],'pho_compress':False},method='PUT')
  mark=len(Model.cache_records);mark_tok=len(Model.tokenizer_requests)
  off=self.send(b,news+' Schalter aus.')
  self.assertEqual(len(Model.tokenizer_requests)-mark_tok,0,'PhO off skips the live tokenizer')
  self.assert_warm_prefix(failed,off,Model.cache_records[mark])
  self.assertIn('Schalter aus.',[m for m in off['messages'] if m.get('role')=='user'][-1]['content'])
  self.assertIn('Bundestag',[m for m in off['messages'] if m.get('role')=='user'][-1]['content'])
  log=self.tmp_path.joinpath('server.log').read_text()
  self.assertIn('pho-compress scope=live model=',log)

class TwoAgentsAtStart(unittest.TestCase):
 """A legacy one-bot agents.json still starts as DeepSeek plus Qwen.

 The banner count is AGENTS after load_agents. MAG_ROOM_MAX_BOTS=2 used to
 only validate the contract, so every mag-120 start logged 1 Agenten.
 """
 HARD_S = 20

 def test_room_start_has_deepseek_and_qwen(self):
  import signal
  def boom(signum, frame):
   raise TimeoutError('hard timeout: room start did not reach 2 agents in %ss' % self.HARD_S)
  previous = signal.signal(signal.SIGALRM, boom)
  signal.alarm(self.HARD_S + 10)
  tmp = tempfile.TemporaryDirectory()
  proc = None
  try:
   root = pathlib.Path(tmp.name)
   state = root / 'state'
   state.mkdir()
   state.joinpath('agents.json').write_text(json.dumps([{
    'id': 'deepseek-chat', 'name': 'DeepSeek Chat', 'vm': 'agent1',
    'model': 'deepseek-v4-flash-0731', 'memory': [{'text': 'bleibt'}],
   }]))
   ui, bridge = port(), port()
   log_path = root / 'server.log'
   env = dict(os.environ,
    MAG_OWNER_CANONICAL_LOGIN='chris@github', MAG_TEST_ALLOW_LOCAL_OWNER='1',
    MAG_ROOM_STATE=str(state), MAG_ROOM_PORT=str(ui), MAG_ROOM_BRIDGE_PORT=str(bridge),
    MAG_ROOM_BRIDGE_IP='127.0.0.1', MAG_ROOM_BACKEND='http://127.0.0.1:9/v1/chat/completions',
    MAG_ROOM_UI=str(ROOT / 'mag-room/web/index.html'),
    MAG_ROOM_STATIC=str(ROOT / 'mag-room/static'),
    MAG_ROOM_MODEL_STATUS=str(root / 'ms.json'), MAG_NETMODE=str(NETMODE),
    MAG_TEST_NO_MEMGIT='1', MAG_ROOM_AGENT_CTL='/bin/false', MAG_ROOM_GUEST_RUN='/bin/false',
    MAG_ROOM_MCP_AUTO='0', MAG_ROOM_PREWARM='0',
    MAG_ROOM_SINGLE_BOT='auto', MAG_ROOM_SINGLE_BOT_VM='on', MAG_ROOM_DUAL_NUMA='1',
    MAG_ROOM_MODEL_ID='deepseek-v4-flash-0731',
    MAG_ROOM_MODEL_LABEL='DeepSeek V4 Flash + Qwen3.8 Uncensored',
    MAG_ROOM_MODEL_PORTS='8191:0,8192:1', MAG_ROOM_MAX_BOTS='2',
    MAG_ROOM_MODEL_ID_0='deepseek-v4-flash-0731', MAG_ROOM_MODEL_LABEL_0='DeepSeek V4 Flash',
    MAG_ROOM_MODEL_ID_1='qwen3.8-flash-next-uncensored', MAG_ROOM_MODEL_LABEL_1='Qwen3.8 Uncensored',
    MAG_ROOM_THINKING='on', MAG_ROOM_PRESETS=str(ROOT / 'ops/room/presets.json'))
   def start():
    nonlocal proc
    log = open(log_path, 'w')
    try:
     # timeout(1) SIGKILLs a stuck magroom even if this thread is blocked.
     proc = subprocess.Popen(
      ['timeout', '-k', '1', '12', str(ROOT / 'build/magroom')],
      env=env, stdout=log, stderr=log)
    finally:
     log.close()
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
     if proc.poll() is not None:
      self.fail('magroom exited %s\n%s' % (proc.returncode, log_path.read_text()[-2000:]))
     try:
      urllib.request.urlopen('http://127.0.0.1:%d/healthz' % ui, timeout=1).read()
      return
     except Exception:
      time.sleep(0.05)
    self.fail('hard timeout waiting for healthz\n%s' % log_path.read_text()[-2000:])
   def stop():
    nonlocal proc
    if proc is None or proc.poll() is not None:
     return
    proc.terminate()
    try:
     proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
     proc.kill()
     proc.wait(timeout=5)
   def agents():
    with urllib.request.urlopen('http://127.0.0.1:%d/api/state' % ui, timeout=2) as r:
     body = json.loads(r.read())
    return body
   binding = ('2 Agenten (DeepSeek V4 Flash=deepseek-v4-flash-0731@127.0.0.1:8191/numa0, '
              'Qwen3.8 Uncensored=qwen3.8-flash-next-uncensored@127.0.0.1:8192/numa1), Gateway ')
   try:
    start()
    banner = log_path.read_text()
    self.assertIn(binding, banner)
    self.assertNotIn('1 Agenten', banner)
    body = agents()
    self.assertTrue(body['configured_model'])
    self.assertEqual(body['single_bot'], '')
    self.assertFalse(body['dual_numa'])
    got = [(a['id'], a['name'], a['slot'], a['model'], a['numa'], a['vm']) for a in body['agents']]
    self.assertEqual(got, [
     ('deepseek-chat', 'DeepSeek V4 Flash', 1, 'deepseek-v4-flash-0731', 0, 'agent1'),
     ('qwen-numa1', 'Qwen3.8 Uncensored', 2, 'qwen3.8-flash-next-uncensored', 1, ''),
    ])
    self.assertEqual([(i['port'], i['numa'], i['model'], i['label']) for i in body['model_instances']],
     [(8191, 0, 'deepseek-v4-flash-0731', 'DeepSeek V4 Flash'),
      (8192, 1, 'qwen3.8-flash-next-uncensored', 'Qwen3.8 Uncensored')])
    stored = json.loads(state.joinpath('agents.json').read_text())
    self.assertEqual(stored[0]['memory'][0]['text'], 'bleibt')
    self.assertEqual(len(stored), 2)
    stop()
    start()
    self.assertIn(binding, log_path.read_text())
    again = agents()['agents']
    self.assertEqual([(a['id'], a['slot'], a['name'], a['model']) for a in again],
     [(a[0], a[2], a[1], a[3]) for a in got])
    self.assertEqual(json.loads(state.joinpath('agents.json').read_text())[0]['memory'][0]['text'], 'bleibt')
    unit = (ROOT / 'ops/mag-120.service').read_text()
    self.assertIn('EnvironmentFile=-/etc/mag-stack/room-model.env\n', unit)
    self.assertIn('Environment=MAG_ROOM_SINGLE_BOT=auto\n', unit)
   finally:
    stop()
  finally:
   signal.alarm(0)
   signal.signal(signal.SIGALRM, previous)
   if proc is not None and proc.poll() is None:
    proc.kill()
    proc.wait(timeout=5)
   tmp.cleanup()

class TwoBackendStartup(unittest.TestCase):
 """Chats from the filled room hit DeepSeek on slot 0 and Qwen on slot 1."""
 def test_starts_with_two_agents_bound_to_both_backends(self):
  deadline=time.monotonic()+25
  def left():
   remain=deadline-time.monotonic()
   self.assertGreater(remain,0,'hard timeout 25s')
   return remain
  Model.delay=0;Model.slot_delay={};Model.script=[];Model.reply=None
  Model.barrier_token='';Model.barrier_release=None;Model.hold_get=0;Model.health=200
  tmp=tempfile.TemporaryDirectory();root=pathlib.Path(tmp.name)
  state=root/'state';state.mkdir()
  state.joinpath('agents.json').write_text(json.dumps([
   {'id':'deepseek-chat','name':'DeepSeek Chat','vm':'agent1','model':'deepseek-v4-flash-0731','memory':[{'text':'bleibt'}]}]))
  model=http.server.ThreadingHTTPServer(('127.0.0.1',0),Model)
  threading.Thread(target=model.serve_forever,daemon=True).start()
  ui,bridge=port(),port()
  log_path=root/'server.log'
  env=dict(os.environ,MAG_OWNER_CANONICAL_LOGIN='chris@github',MAG_TEST_ALLOW_LOCAL_OWNER='1',
    MAG_ROOM_STATE=str(state),MAG_ROOM_PORT=str(ui),MAG_ROOM_BRIDGE_PORT=str(bridge),
    MAG_ROOM_BRIDGE_IP='127.0.0.1',MAG_ROOM_BACKEND=f'http://127.0.0.1:{model.server_port}/v1/chat/completions',
    MAG_ROOM_UI=str(ROOT/'mag-room/web/index.html'),MAG_ROOM_STATIC=str(ROOT/'mag-room/static'),
    MAG_ROOM_MODEL_STATUS=str(root/'ms.json'),MAG_NETMODE=str(NETMODE),MAG_TEST_NO_MEMGIT='1',
    MAG_ROOM_AGENT_CTL='/bin/false',MAG_ROOM_GUEST_RUN='/bin/false',MAG_ROOM_MCP_AUTO='0',MAG_ROOM_PREWARM='0',
    MAG_ROOM_SINGLE_BOT='auto',MAG_ROOM_SINGLE_BOT_VM='on',MAG_ROOM_DUAL_NUMA='0',
    MAG_ROOM_MODEL_ID='deepseek-v4-flash-0731',MAG_ROOM_MODEL_LABEL='DeepSeek V4 Flash + Qwen3.8 Uncensored',
    MAG_ROOM_MODEL_PORTS='8191:0,8192:1',MAG_ROOM_MAX_BOTS='2',MAG_ROOM_PRESETS=str(ROOT/'ops/room/presets.json'),
    MAG_ROOM_MODEL_ID_0='deepseek-v4-flash-0731',MAG_ROOM_MODEL_LABEL_0='DeepSeek V4 Flash',
    MAG_ROOM_MODEL_ID_1='qwen3.8-flash-next-uncensored',MAG_ROOM_MODEL_LABEL_1='Qwen3.8 Uncensored',
    MAG_ROOM_THINKING='on')
  log=open(log_path,'a')
  proc=subprocess.Popen(['timeout','-k','1','20',str(ROOT/'build/magroom')],env=env,stdout=log,stderr=log)
  def request(path,body=None,method=None,raw=False):
   data=json.dumps(body).encode() if body is not None else None
   headers={'Content-Type':'application/json'} if data is not None else {}
   m=(method or ('POST' if data is not None else 'GET')).upper()
   if m not in ('GET','HEAD') and not raw:headers['X-MAG-Token']=_ui_token(ui)
   req=urllib.request.Request(f'http://127.0.0.1:{ui}'+path,data=data,method=method,headers=headers)
   with urllib.request.urlopen(req,timeout=max(0.2,min(3.0,left()))) as r:
    blob=r.read()
    return blob if raw else json.loads(blob)
  try:
   while True:
    try:
     self.assertEqual(request('/healthz',raw=True),b'ok\n');break
    except AssertionError:raise
    except Exception:
     if proc.poll() is not None:self.fail('magroom exited %s\n%s'%(proc.returncode,log_path.read_text(errors='replace')[-2000:]))
     time.sleep(.03)
   started=log_path.read_text(errors='replace')
   self.assertIn('2 Agenten (DeepSeek V4 Flash=deepseek-v4-flash-0731@127.0.0.1:8191/numa0, Qwen3.8 Uncensored=qwen3.8-flash-next-uncensored@127.0.0.1:8192/numa1), Gateway ',started)
   self.assertNotIn('1 Agenten',started)
   state_doc=request('/api/state')
   bots=state_doc['agents']
   self.assertEqual([(b['slot'],b['id'],b['name'],b['model'],b['numa'],b['vm']) for b in bots],[
    (1,'deepseek-chat','DeepSeek V4 Flash','deepseek-v4-flash-0731',0,'agent1'),
    (2,'qwen-numa1','Qwen3.8 Uncensored','qwen3.8-flash-next-uncensored',1,'')])
   self.assertEqual(json.loads(state.joinpath('agents.json').read_text())[0]['memory'][0]['text'],'bleibt')
   for bot,slot in (('deepseek-chat','0'),('qwen-numa1','1')):
    token='Bind %s %s'%(bot,time.monotonic_ns())
    request('/api/message',{'text':token,'bots':[bot]})
    hit=None
    while hit is None:
     left()
     for call,got in list(Model.pairs):
      if token in json.dumps(call,ensure_ascii=False):hit=(call,got);break
     if hit is None:time.sleep(.02)
    self.assertEqual(hit[1],slot)
    self.assertEqual(hit[0]['model'],bots[int(slot)]['model'])
    self.assertEqual(hit[0]['id_slot'],0)
  finally:
   proc.terminate()
   try:proc.wait(timeout=5)
   except subprocess.TimeoutExpired:proc.kill();proc.wait(timeout=5)
   log.close();model.shutdown();model.server_close();tmp.cleanup()

class GlmLayoutStartup(unittest.TestCase):
 """Layout glm: both GLM bots preserve their historical IDs and memory."""
 def test_q1_q2_follow_glm_backend(self):
  deadline=time.monotonic()+20
  Model.delay=0;Model.slot_delay={};Model.script=[];Model.reply=None
  Model.barrier_token='';Model.barrier_release=None;Model.hold_get=0;Model.health=200
  Model.saved_prompt.clear();Model.slot_snapshot=[];Model.restore_missing=True
  # Both backends are GLM now: do not block NUMA0's only slot.
  Model.slot_block={}
  mark=len(Model.paths)
  tmp=tempfile.TemporaryDirectory();root=pathlib.Path(tmp.name)
  state=root/'state';state.mkdir()
  state.joinpath('agents.json').write_text(json.dumps([
   {'id':'deepseek-chat','name':'DeepSeek Chat','vm':'','slot':1,'numa':0,'model':'deepseek-v4-flash-0731','memory':[]},
   {'id':'qwen-numa1','name':'Qwen','vm':'','slot':2,'numa':1,'model':'qwen3.8-flash-next-uncensored','memory':[{'text':'bleibt'}]}]))
  state.joinpath('prompt-prefix.json').write_text(json.dumps({
   '8192/mag-prefix-glm-5.3-flash-slot2-chat-v2-prefix.bin':'glm-5.3-flash:stale'}))
  model=http.server.ThreadingHTTPServer(('127.0.0.1',0),Model)
  threading.Thread(target=model.serve_forever,daemon=True).start()
  ui,bridge=port(),port()
  log_path=root/'server.log'
  glm={}
  for line in (ROOT/'ops/room/glm.env').read_text().splitlines():
   parts=shlex.split(line,comments=True)
   if parts and '=' in parts[0]:
    k,v=parts[0].split('=',1);glm[k]=v
  glm['MAG_ROOM_PRESETS']=str(ROOT/'ops/room/presets.json');glm['MAG_ROOM_PREWARM']='1'
  env=dict(os.environ,MAG_OWNER_CANONICAL_LOGIN='chris@github',MAG_TEST_ALLOW_LOCAL_OWNER='1',
    MAG_ROOM_STATE=str(state),MAG_ROOM_PORT=str(ui),MAG_ROOM_BRIDGE_PORT=str(bridge),
    MAG_ROOM_BRIDGE_IP='127.0.0.1',MAG_ROOM_BACKEND=f'http://127.0.0.1:{model.server_port}/v1/chat/completions',
    MAG_ROOM_UI=str(ROOT/'mag-room/web/index.html'),MAG_ROOM_STATIC=str(ROOT/'mag-room/static'),
    MAG_ROOM_MODEL_STATUS=str(root/'ms.json'),MAG_NETMODE=str(NETMODE),MAG_TEST_NO_MEMGIT='1',
    MAG_ROOM_AGENT_CTL='/bin/false',MAG_ROOM_GUEST_RUN='/bin/false',MAG_ROOM_MCP_AUTO='0',
    MAG_ROOM_SINGLE_BOT='auto',MAG_ROOM_SINGLE_BOT_VM='on',MAG_ROOM_DUAL_NUMA='0',**glm)
  env['MAG_ROOM_SKILLS_DIR']=str(ROOT/'ops/room/skills')
  log=open(log_path,'a')
  proc=subprocess.Popen(['timeout','-k','1','35',str(ROOT/'build/magroom')],env=env,stdout=log,stderr=log)
  try:
   while True:
    self.assertGreater(deadline-time.monotonic(),0,'room did not start')
    try:
     with urllib.request.urlopen(f'http://127.0.0.1:{ui}/api/state',timeout=2) as r:doc=json.loads(r.read());break
    except Exception:
     if proc.poll() is not None:self.fail('magroom exited %s\n%s'%(proc.returncode,log_path.read_text(errors='replace')[-2000:]))
     time.sleep(.03)
   self.assertEqual([(b['slot'],b['id'],b['name'],b['model'],b['numa']) for b in doc['agents']],[
    (1,'deepseek-chat','GLM-Q1','glm-5.3-flash-iq1-m',0),
    (2,'qwen-numa1','GLM-Q2','glm-5.3-flash',1)])
   saved=json.loads(state.joinpath('agents.json').read_text())
   self.assertEqual([b['name'] for b in saved[:2]],['GLM-Q1','GLM-Q2'])
   self.assertEqual(saved[1]['memory'][0]['text'],'bleibt')
   end=time.monotonic()+10
   while time.monotonic()<end:
    paths=list(Model.paths)[mark:]
    glm_files=[p for p in paths if p[3].get('filename')=='mag-prefix-glm-5.3-flash-slot2-chat-v2-prefix.bin']
    if any(p[0].endswith('save') for p in glm_files):break
    time.sleep(.05)
   else:self.fail('GLM cold prefix not saved: '+log_path.read_text()[-1200:])
   self.assertEqual(sum(p[0].endswith('restore') for p in glm_files),1,glm_files)
   self.assertTrue(any(p[0]=='/v1/chat/completions' and p[3].get('model')=='glm-5.3-flash' and p[3].get('max_tokens')==1 for p in paths))
   time.sleep(.3)
   self.assertEqual(sum(p[0].endswith('restore') for p in list(Model.paths)[mark:] if p[3].get('filename')=='mag-prefix-glm-5.3-flash-slot2-chat-v2-prefix.bin'),1,'infinite restore')
   token=_ui_token(ui)
   def post(endpoint,payload,method='POST'):
    req=urllib.request.Request(f'http://127.0.0.1:{ui}{endpoint}',data=json.dumps(payload).encode(),
                               method=method,headers={'Content-Type':'application/json','X-MAG-Token':token})
    with urllib.request.urlopen(req,timeout=5) as resp:return json.load(resp)
   # Historical DeepSeek bot ID now uses GLM-Q1 on NUMA0.
   first='GLM-FIRST-HI-'+str(time.monotonic_ns())
   post('/api/message',{'text':first,'bots':['deepseek-chat'],'request_id':'glm-first-hi'})
   end=time.monotonic()+5
   while time.monotonic()<end:
    first_calls=[p for p in list(Model.paths)[mark:] if p[0]=='/v1/chat/completions'
                  and p[3].get('max_tokens')!=1 and first in json.dumps(p[3])]
    if first_calls:break
    time.sleep(.05)
   else:self.fail('GLM-Q1 did not receive first turn')
   self.assertEqual([p[3]['model'] for p in first_calls],['glm-5.3-flash-iq1-m'])
   # llama --parallel 1 uses id_slot=0, while the checkpoint filename's
   # The MAG room-agent slot and llama id_slot are different namespaces.
   self.assertEqual(first_calls[0][3].get('id_slot'),0)
   # The cached system/tools body is followed by a bounded, two-message chat.
   self.assertEqual(len(first_calls[0][3]['messages']),2)
   self.assertFalse(first_calls[0][3]['chat_template_kwargs']['enable_thinking'])
   self.assertLessEqual(len(first_calls[0][3]['messages'][1]['content'].encode()),700)
   self.assertEqual((state/'glm-chat-defaults-v1').read_text().strip(),'1')
   self.assertEqual((state/'reflect').read_text(),'off')
   self.assertEqual((state/'autotalk').read_text(),'off')
   req=first_calls[0][3]
   self.assertNotIn('tools',req,'GLM chat must never send tools schemas')
   self.assertNotIn('tool_choice',req,'GLM chat must omit tool choice too')
   self.assertEqual(req['max_tokens'],256)
   self.assertEqual(req['chat_template_kwargs']['reasoning_effort'],'low')
   self.assertTrue(req['chat_template_kwargs']['clear_thinking'])
   sys=req['messages'][0]['content']
   self.assertIn('[Identitaet]',sys)
   self.assertIn('Du hast keine Werkzeuge',sys)
   self.assertNotIn('todo(',sys)
   self.assertNotIn('run_command',sys)
   self.assertNotIn('DeepSeek',sys)
   self.assertNotIn('Qwen',sys)
   self.assertLessEqual(len(sys.encode('utf-8'))//3,900)
   self.assertNotIn('todo(',json.dumps(req,ensure_ascii=False))
   self.assertNotIn('run_command',json.dumps(req,ensure_ascii=False))
   self.assertTrue(any(p[0]=='/v1/chat/completions' and
       p[3].get('max_tokens')==1 and
       'tools' not in p[3] for p in paths),
       'prewarm uses the same no-tools system prefix')
   # Host-only memory mutations must not trigger the model.
   count_before=len([p for p in Model.paths if p[0]=='/v1/chat/completions' and
                     p[3].get('max_tokens')!=1])
   self.assertTrue(post('/api/message',{'text':'/merk Test-Fakt-ZQ',
       'bots':['qwen-numa1']})['ok'])
   persisted=json.loads((state/'agents.json').read_text())
   self.assertTrue(any('Test-Fakt-ZQ' in str(v) for v in persisted[1].get('memory',[])))
   self.assertEqual(count_before,len([p for p in Model.paths
       if p[0]=='/v1/chat/completions' and p[3].get('max_tokens')!=1]))
   # The last short greeting, not accumulated room text, enters user message.
   before=len(Model.paths)
   post('/api/message',{'text':'Hi','bots':['qwen-numa1'],
       'request_id':'glm-short-memory'})
   end=time.monotonic()+6
   while time.monotonic()<end:
    greetings=[p for p in list(Model.paths)[before:]
               if p[0]=='/v1/chat/completions' and
               p[3].get('max_tokens')!=1]
    if greetings:break
    time.sleep(.05)
   else:self.fail('GLM short greeting missing')
   self.assertEqual(greetings[0][3]['messages'][1]['content'],'Hi')
   self.assertIn('Test-Fakt-ZQ',greetings[0][3]['messages'][0]['content'])
   self.assertNotIn('tools',greetings[0][3])
   self.assertFalse(greetings[0][3]['chat_template_kwargs']['enable_thinking'])
   self.assertTrue(post('/api/message',{'text':'/vergiss 1',
       'bots':['qwen-numa1']})['ok'])
   # Host-selected skill is a user suffix; not system/tool catalog.
   before=len(Model.paths)
   post('/api/message',{'text':'/skill kurz','bots':['qwen-numa1'],
       'request_id':'glm-skill-kurz'})
   end=time.monotonic()+6
   while time.monotonic()<end:
    skill_calls=[p for p in list(Model.paths)[before:]
                 if p[0]=='/v1/chat/completions' and
                 p[3].get('max_tokens')!=1 and
                 '[Skill: kurz]' in str(p[3].get('messages'))]
    if skill_calls:break
    time.sleep(.05)
   else:self.fail('GLM skill was not selected')
   self.assertNotIn('[Skill:',skill_calls[0][3]['messages'][0]['content'])
   self.assertNotIn('tools',skill_calls[0][3])
   # Explicit thinking-limit error gets exactly one tool-free final retry.
   retry_marker='GLM-RETRY-'+str(time.monotonic_ns())
   Model.script_scope=retry_marker
   Model.script=[
    [{'_chunk':{'choices':[{'delta':{'reasoning_content':'nur Gedanke'},
                            'finish_reason':'length'}]}}],
    ['RETRY-OK']]
   before=len(Model.paths)
   try:
    post('/api/message',{'text':retry_marker,'bots':['deepseek-chat'],
        'think':True,'request_id':'glm-one-thinking-retry'})
    end=time.monotonic()+7
    while time.monotonic()<end:
     retry_calls=[p for p in list(Model.paths)[before:]
                  if p[0]=='/v1/chat/completions' and
                  p[3].get('max_tokens')!=1 and retry_marker in json.dumps(p[3])]
     if len(retry_calls)>=2:break
     time.sleep(.05)
    else:self.fail('GLM thinking retry did not run exactly once')
    self.assertEqual(len(retry_calls),2)
    self.assertTrue(retry_calls[0][3]['chat_template_kwargs']['enable_thinking'])
    self.assertFalse(retry_calls[1][3]['chat_template_kwargs']['enable_thinking'])
    self.assertEqual([p[3]['max_tokens'] for p in retry_calls],[256,256])
    self.assertEqual(retry_calls[0][3]['messages'],retry_calls[1][3]['messages'])
    self.assertNotIn('tools',retry_calls[1][3])
   finally:
    Model.script_scope=None
    Model.script=[]
   # Default unaddressed messages route to only one enabled GLM bot.
   before=len(Model.paths)
   marker='GLM-DEFAULT-'+str(time.monotonic_ns())
   post('/api/message',{'text':marker,'request_id':'glm-one-default'})
   end=time.monotonic()+6
   while time.monotonic()<end:
    default_calls=[p for p in list(Model.paths)[before:]
                   if p[0]=='/v1/chat/completions' and
                   p[3].get('max_tokens')!=1 and marker in json.dumps(p[3])]
    if default_calls:break
    time.sleep(.05)
   else:self.fail('GLM default bot did not answer')
   time.sleep(.15)
   self.assertEqual(len(default_calls),1,default_calls)
   self.assertEqual(default_calls[0][3]['model'],'glm-5.3-flash-iq1-m')
   # The UI sends the enabled roster, which must not broadcast to both.
   before=len(Model.paths)
   roster_marker='GLM-ROSTER-'+str(time.monotonic_ns())
   post('/api/message',{'text':roster_marker,'bots':['deepseek-chat','qwen-numa1']})
   end=time.monotonic()+6
   while time.monotonic()<end:
    roster_calls=[p for p in list(Model.paths)[before:]
                  if p[0]=='/v1/chat/completions' and p[3].get('max_tokens')!=1
                  and roster_marker in json.dumps(p[3])]
    if roster_calls:break
    time.sleep(.05)
   else:self.fail('GLM default roster did not answer')
   time.sleep(.15)
   self.assertEqual([p[3]['model'] for p in roster_calls],['glm-5.3-flash-iq1-m'])

   # No DeepSeek cold gate: Q1 and Q2 are separately routable.
   post('/api/bot-settings',{'bots':['deepseek-chat'],'enabled':False},'PUT')
   paused_mark='GLM-EXPLICIT-PAUSED-'+str(time.monotonic_ns())
   before=len(Model.paths)
   self.assertTrue(post('/api/message',{'text':paused_mark,
       'bots':['deepseek-chat']})['accepted'])
   time.sleep(.2)
   self.assertFalse([p for p in list(Model.paths)[before:]
       if p[0]=='/v1/chat/completions' and p[3].get('max_tokens')!=1
       and paused_mark in json.dumps(p[3])])
   marker='GLM-PAUSED-'+str(time.monotonic_ns())
   post('/api/message',{'text':marker,'bots':['deepseek-chat','qwen-numa1']})
   end=time.monotonic()+10
   while time.monotonic()<end:
    hits=[p for p in list(Model.paths)[mark:] if p[0]=='/v1/chat/completions' and p[3].get('max_tokens')!=1 and marker in json.dumps(p[3])]
    if hits:break
    time.sleep(.05)
   else:self.fail('GLM turn blocked after missing checkpoint')
   time.sleep(.2)
   self.assertEqual(len(hits),1,hits)
   self.assertEqual(hits[0][3].get('model'),'glm-5.3-flash')
   # Reverse: Q2 paused and Q1 enabled. Only GLM-Q1 may answer.
   post('/api/bot-settings',{'bots':['deepseek-chat'],'enabled':True},'PUT')
   post('/api/bot-settings',{'bots':['qwen-numa1'],'enabled':False},'PUT')
   second='DEEPSEEK-GLM-PAUSED-'+str(time.monotonic_ns())
   post('/api/message',{'text':second,'bots':['deepseek-chat','qwen-numa1'],'request_id':'glm-pause-reverse'})
   end=time.monotonic()+7
   while time.monotonic()<end:
    second_calls=[p for p in list(Model.paths)[mark:] if p[0]=='/v1/chat/completions'
                  and p[3].get('max_tokens')!=1 and second in json.dumps(p[3])]
    if second_calls:break
    time.sleep(.05)
   else:self.fail('GLM-Q1 did not answer when Q2 was paused')
   time.sleep(.2)
   self.assertEqual([p[3]['model'] for p in second_calls],['glm-5.3-flash-iq1-m'])
   # Both paused: publish the user message once, with no fallback/model call.
   post('/api/bot-settings',{'bots':['deepseek-chat'],'enabled':False},'PUT')
   no_one='BOTH-PAUSED-'+str(time.monotonic_ns())
   body={'text':no_one,'bots':['deepseek-chat','qwen-numa1'],'request_id':'glm-both-paused'}
   self.assertTrue(post('/api/message',body)['accepted'])
   self.assertTrue(post('/api/message',body).get('duplicate'))
   time.sleep(.35)
   no_calls=[p for p in list(Model.paths)[mark:] if p[0]=='/v1/chat/completions'
             and p[3].get('max_tokens')!=1 and no_one in json.dumps(p[3])]
   self.assertEqual(no_calls,[],'both paused must not infer')
   state_doc=json.load(urllib.request.urlopen(f'http://127.0.0.1:{ui}/api/state',timeout=2))
   user_at=[i for i,m in enumerate(state_doc['room']) if m.get('role')=='user' and m.get('content')==no_one]
   self.assertEqual(len(user_at),1,'duplicate human room message')
   self.assertFalse(any(m.get('role')=='assistant' for m in state_doc['room'][user_at[0]+1:]),
                    'both paused must not append bot response')
  finally:
   Model.slot_block={}
   Model.restore_missing=False
   proc.terminate()
   try:proc.wait(timeout=5)
   except subprocess.TimeoutExpired:proc.kill();proc.wait(timeout=5)
   log.close();model.shutdown();model.server_close();tmp.cleanup()


 def test_glm_chat_stops_stream_and_routes_greetings(self):
  # Fast fake llama streams, real GLM room process; no model restart or flags.
  Model.delay=0;Model.slot_delay={};Model.script=[];Model.reply=None
  Model.script_scope=None;Model.slot_block={};Model.restore_missing=False
  Model.barrier_token='';Model.barrier_release=None;Model.hold_get=0
  tmp=tempfile.TemporaryDirectory();root=pathlib.Path(tmp.name)
  state=root/'state';state.mkdir()
  state.joinpath('agents.json').write_text(json.dumps([
   {'id':'deepseek-chat','name':'GLM-Q1','slot':1,'numa':0,'vm':'','memory':[]},
   {'id':'qwen-numa1','name':'GLM-Q2','slot':2,'numa':1,'vm':'','memory':[]}]))
  model=http.server.ThreadingHTTPServer(('127.0.0.1',0),Model)
  threading.Thread(target=model.serve_forever,daemon=True).start()
  ui,bridge=port(),port();glm={}
  for line in (ROOT/'ops/room/glm.env').read_text().splitlines():
   parts=shlex.split(line,comments=True)
   if parts and '=' in parts[0]:
    key,value=parts[0].split('=',1);glm[key]=value
  glm['MAG_ROOM_PREWARM']='0'
  glm['MAG_ROOM_PRESETS']=str(ROOT/'ops/room/presets.json')
  env=dict(os.environ,MAG_OWNER_CANONICAL_LOGIN='chris@github',
   MAG_TEST_ALLOW_LOCAL_OWNER='1',MAG_ROOM_STATE=str(state),
   MAG_ROOM_PORT=str(ui),MAG_ROOM_BRIDGE_PORT=str(bridge),
   MAG_ROOM_BRIDGE_IP='127.0.0.1',
   MAG_ROOM_BACKEND=f'http://127.0.0.1:{model.server_port}/v1/chat/completions',
   MAG_ROOM_UI=str(ROOT/'mag-room/web/index.html'),MAG_ROOM_STATIC=str(ROOT/'mag-room/static'),
   MAG_ROOM_MODEL_STATUS=str(root/'ms.json'),MAG_NETMODE=str(NETMODE),
   MAG_TEST_NO_MEMGIT='1',MAG_ROOM_AGENT_CTL='/bin/false',
   MAG_ROOM_GUEST_RUN='/bin/false',MAG_ROOM_MCP_AUTO='0',
   MAG_ROOM_SINGLE_BOT='auto',MAG_ROOM_SINGLE_BOT_VM='on',
   MAG_ROOM_DUAL_NUMA='0',**glm)
  log=open(root/'room.log','a')
  proc=subprocess.Popen(['timeout','-k','1','29',str(ROOT/'build/magroom')],
                        env=env,stdout=log,stderr=log)
  try:
   wait_http(f'http://127.0.0.1:{ui}/api/state',timeout=8,proc=proc)
   token=_ui_token(ui)
   def post(body):
    req=urllib.request.Request(f'http://127.0.0.1:{ui}/api/message',
      data=json.dumps(body).encode(),
      headers={'Content-Type':'application/json','X-MAG-Token':token})
    with urllib.request.urlopen(req,timeout=4) as resp:return json.load(resp)
   def state_():
    with urllib.request.urlopen(f'http://127.0.0.1:{ui}/api/state',timeout=2) as resp:
     return json.load(resp)
   def calls_after(start,marker,count):
    end=time.monotonic()+6
    while time.monotonic()<end:
     calls=[p for p in list(Model.paths)[start:]
            if p[0]=='/v1/chat/completions' and p[3].get('max_tokens')!=1
            and marker in json.dumps(p[3])]
     if len(calls)>=count:return calls
     time.sleep(.03)
    self.fail('Missing GLM backend calls for '+marker)
   def idle_after(marker):
    end=time.monotonic()+7
    while time.monotonic()<end:
     room=state_()
     if (not room['busy'] and not room['pending'] and
         any(m.get('role')=='user' and marker in (m.get('content') or '')
             for m in room['room'])):
      return room
     time.sleep(.04)
    self.fail('GLM turn did not finish: '+marker)
   # Stop strings are llama.cpp /v1/chat/completions request parameters, not flags.
   # Multiple SSE fragments deliberately split each forbidden token.
   for n,parts in enumerate((
      ['5','</thi','nk>','GARBAGE-THINK-END'],
      ['<thi','nk>','GARBAGE-THINK-OPEN'],
      ['5','<|us','er|>','GARBAGE-SPECIAL'],
      ['5','\n\n#','# ','GARBAGE-HEADING'])):
    marker='GLM-CUT-'+str(n)+'-'+str(time.monotonic_ns())
    Model.script_scope=marker
    Model.script=[parts]
    before=len(Model.paths)
    self.assertTrue(post({'text':marker,'bots':['deepseek-chat']})['accepted'])
    req=calls_after(before,marker,1)[0][3]
    self.assertLessEqual(req['max_tokens'],256)
    self.assertTrue({'</think>','<think>','<|user|>','<|observation|>',
                     '\n\n## '}.issubset(set(req['stop'])),req)
    room=idle_after(marker)
    text=json.dumps(room['room'],ensure_ascii=False)
    self.assertNotIn('GARBAGE-',text)
    self.assertNotIn('</think>',text)
    self.assertNotIn('<think>',text)
    self.assertNotIn('<|user|>',text)
    if n!=1:
     self.assertTrue(any(m.get('role')=='assistant' and
           (m.get('content') or '').strip()=='5'
           for m in room['room']),room['room'][-3:])
    Model.script_scope=None
    Model.script=[]
   # A single leading greeting + group target broadcasts, but a middle
   # mention of 'beide' must retain GLM's one-host arbitration.
   for text_,expected in [('Hi beide: wie viel ist 2+3?',2),
                          ('ich mag beide Farben',1)]:
    marker='GLM-GROUP-'+str(time.monotonic_ns())
    before=len(Model.paths)
    self.assertTrue(post({'text':text_+' '+marker,
                          'bots':['deepseek-chat','qwen-numa1']})['accepted'])
    calls=calls_after(before,marker,expected)
    idle_after(marker)
    self.assertEqual(len(calls),expected,calls)
   # Forgetting memory indexes across multiple agents is ambiguous.
   for ids in (['deepseek-chat','qwen-numa1'],None):
    body={'text':'/vergiss 1'}
    if ids is not None:body['bots']=ids
    with self.assertRaises(urllib.error.HTTPError) as error:
     post(body)
    self.assertEqual(error.exception.code,400)
    self.assertIn('Für /vergiss genau einen Bot wählen',
                  error.exception.read().decode())
  finally:
   Model.script_scope=None;Model.script=[]
   proc.terminate()
   try:proc.wait(timeout=5)
   except subprocess.TimeoutExpired:proc.kill();proc.wait(timeout=5)
   log.close();model.shutdown();model.server_close();tmp.cleanup()


 def test_two_glm_turns_complete_after_60_seconds_without_cancel(self):
  # Explicit @alle must reach both GLM lanes, with each fake under 8 s.
  # No real inference; keep the CI per-test deadline at 30 seconds.
  Model.delay=0;Model.slot_delay={'0':2,'1':2};Model.script=[]
  Model.reply=None;Model.health=200;Model.slot_snapshot=[];Model.paths=[]
  Model.slot_block={};Model.barrier_release=None;Model.restore_missing=False
  tmp=tempfile.TemporaryDirectory();root=pathlib.Path(tmp.name);state=root/'state';state.mkdir()
  state.joinpath('agents.json').write_text(json.dumps([
   {'id':'deepseek-chat','name':'GLM-Q1','slot':1,'numa':0,'vm':'','memory':[]},
   {'id':'qwen-numa1','name':'GLM-Q2','slot':2,'numa':1,'vm':'','memory':[]}]))
  model=http.server.ThreadingHTTPServer(('127.0.0.1',0),Model)
  threading.Thread(target=model.serve_forever,daemon=True).start()
  ui,bridge=port(),port();glm={}
  for line in (ROOT/'ops/room/glm.env').read_text().splitlines():
   parts=shlex.split(line,comments=True)
   if parts and '=' in parts[0]:
    key,value=parts[0].split('=',1);glm[key]=value
  glm['MAG_ROOM_PREWARM']='0'
  glm['MAG_ROOM_PRESETS']=str(ROOT/'ops/room/presets.json')  # CI, not live /opt path
  env=dict(os.environ,MAG_OWNER_CANONICAL_LOGIN='chris@github',MAG_TEST_ALLOW_LOCAL_OWNER='1',
   MAG_ROOM_STATE=str(state),MAG_ROOM_PORT=str(ui),MAG_ROOM_BRIDGE_PORT=str(bridge),
   MAG_ROOM_BRIDGE_IP='127.0.0.1',MAG_ROOM_BACKEND=f'http://127.0.0.1:{model.server_port}/v1/chat/completions',
   MAG_ROOM_UI=str(ROOT/'mag-room/web/index.html'),MAG_ROOM_STATIC=str(ROOT/'mag-room/static'),
   MAG_ROOM_MODEL_STATUS=str(root/'ms.json'),MAG_NETMODE=str(NETMODE),MAG_TEST_NO_MEMGIT='1',
   MAG_ROOM_AGENT_CTL='/bin/false',MAG_ROOM_GUEST_RUN='/bin/false',MAG_ROOM_MCP_AUTO='0',
   MAG_ROOM_SINGLE_BOT='auto',MAG_ROOM_SINGLE_BOT_VM='on',MAG_ROOM_DUAL_NUMA='0',**glm)
  logpath=root/'room.log';log=open(logpath,'a')
  proc=subprocess.Popen(['timeout','-k','1','29',str(ROOT/'build/magroom')],env=env,stdout=log,stderr=log)
  try:
   started=time.monotonic()
   while time.monotonic()-started<18:
    if proc.poll() is not None:
     self.fail('room exited during startup: '+logpath.read_text(errors='replace')[-2000:])
    try:
     with urllib.request.urlopen(f'http://127.0.0.1:{ui}/api/state',timeout=1):break
    except Exception:time.sleep(.05)
   else:self.fail('room startup timeout; log: '+logpath.read_text(errors='replace')[-2000:])
   token=_ui_token(ui)
   req=urllib.request.Request(f'http://127.0.0.1:{ui}/api/message',
      data=json.dumps({'text':'@alle Hi','bots':['deepseek-chat','qwen-numa1']}).encode(),
      headers={'Content-Type':'application/json','X-MAG-Token':token})
   with urllib.request.urlopen(req,timeout=4) as resp:self.assertTrue(json.load(resp)['accepted'])
   end=time.monotonic()+16
   while time.monotonic()<end:
    with urllib.request.urlopen(f'http://127.0.0.1:{ui}/api/state',timeout=3) as resp:st=json.load(resp)
    answers=[m for m in st.get('room',[]) if m.get('role')=='assistant' and
             'Hallo' in (m.get('content') or '')]
    if len(answers)==2:break
    time.sleep(.2)
   else:self.fail('two explicitly requested GLM streams did not complete')
   calls=[p for p in Model.paths if p[0]=='/v1/chat/completions' and p[3].get('max_tokens')!=1]
   self.assertEqual({p[1] for p in calls},{'0','1'})
   self.assertFalse([p for p in Model.paths if 'action=cancel' in p[0]])
   log.flush()
   self.assertNotIn('reason=stop',logpath.read_text(errors='replace'))
  finally:
   Model.slot_delay={}
   proc.terminate()
   try:proc.wait(timeout=5)
   except subprocess.TimeoutExpired:proc.kill();proc.wait(timeout=5)
   log.close();model.shutdown();model.server_close();tmp.cleanup()

class QwenFirstHi(unittest.TestCase):
 """When DeepSeek is busy or cold, Qwen answers the first hi in under 5s."""
 HARD_S = 20

 def test_qwen_answers_first_hi_while_deepseek_is_busy(self):
  import signal, re
  def boom(signum, frame):
   raise TimeoutError('hard timeout: first hi did not finish in %ss' % self.HARD_S)
  previous = signal.signal(signal.SIGALRM, boom)
  signal.alarm(self.HARD_S + 8)
  tmp = tempfile.TemporaryDirectory()
  proc = None
  httpd = None
  hold = threading.Event()
  Model.slot_block = {'0': hold}
  Model.paths = []
  Model.calls = []
  Model.pairs = []
  try:
   root = pathlib.Path(tmp.name)
   state = root / 'state'
   state.mkdir()
   state.joinpath('agents.json').write_text(json.dumps([
    {'id': 'deepseek-chat', 'name': 'DeepSeek Chat', 'vm': '', 'slot': 1,
     'numa': 0, 'model': 'deepseek-v4-flash-0731', 'memory': []},
    {'id': 'qwen-numa1', 'name': 'Qwen', 'vm': '', 'slot': 2,
     'numa': 1, 'model': 'qwen3.8-flash-next-uncensored', 'memory': []},
   ]))
   state.joinpath('access.json').write_text(json.dumps({
    'owners': ['chris@github'], 'owner_name': 'Chris'}))
   httpd = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Model)
   model_port = httpd.server_address[1]
   threading.Thread(target=httpd.serve_forever, daemon=True).start()
   ui, bridge = port(), port()
   log_path = root / 'server.log'
   env = dict(os.environ,
    MAG_OWNER_CANONICAL_LOGIN='chris@github', MAG_TEST_ALLOW_LOCAL_OWNER='1',
    MAG_ROOM_STATE=str(state), MAG_ROOM_PORT=str(ui), MAG_ROOM_BRIDGE_PORT=str(bridge),
    MAG_ROOM_BRIDGE_IP='127.0.0.1',
    MAG_ROOM_BACKEND='http://127.0.0.1:%d/v1/chat/completions' % model_port,
    MAG_ROOM_UI=str(ROOT / 'mag-room/web/index.html'),
    MAG_ROOM_STATIC=str(ROOT / 'mag-room/static'),
    MAG_ROOM_MODEL_STATUS=str(root / 'ms.json'), MAG_NETMODE=str(NETMODE),
    MAG_TEST_NO_MEMGIT='1', MAG_ROOM_AGENT_CTL='/bin/false',
    MAG_ROOM_GUEST_RUN='/bin/false', MAG_ROOM_MCP_AUTO='0', MAG_ROOM_PREWARM='1',
    MAG_ROOM_PREFIX_DISK='0',
    MAG_ROOM_SINGLE_BOT='', MAG_ROOM_DUAL_NUMA='0', MAG_ROOM_THINKING='off',
    MAG_ROOM_MODEL_ID='deepseek-v4-flash-0731',
    MAG_ROOM_MODEL_LABEL='DeepSeek V4 Flash + Qwen3.8 Uncensored',
    MAG_ROOM_MODEL_PORTS='8191:0,8192:1', MAG_ROOM_MAX_BOTS='2',
    MAG_ROOM_MODEL_ID_0='deepseek-v4-flash-0731', MAG_ROOM_MODEL_LABEL_0='DeepSeek V4 Flash',
    MAG_ROOM_MODEL_ID_1='qwen3.8-flash-next-uncensored', MAG_ROOM_MODEL_LABEL_1='Qwen3.8 Uncensored',
    MAG_ROOM_PRESETS=str(ROOT / 'ops/room/presets.json'))
   log = open(log_path, 'w')
   try:
    proc = subprocess.Popen(
     ['timeout', '-k', '1', '16', str(ROOT / 'build/magroom')],
     env=env, stdout=log, stderr=log)
   finally:
    log.close()
   deadline = time.monotonic() + 8
   while time.monotonic() < deadline:
    if proc.poll() is not None:
     self.fail('magroom exited %s\n%s' % (proc.returncode, log_path.read_text()[-2000:]))
    try:
     urllib.request.urlopen('http://127.0.0.1:%d/healthz' % ui, timeout=1).read()
     break
    except Exception:
     time.sleep(0.05)
   else:
    self.fail('hard timeout waiting for healthz\n%s' % log_path.read_text()[-2000:])
   html = urllib.request.urlopen('http://127.0.0.1:%d/' % ui, timeout=2).read().decode()
   tok = re.search(r'name="mag-token" content="([^"]+)"', html).group(1)
   def api(path, body=None, timeout=2):
    data = json.dumps(body).encode() if body is not None else None
    headers = {'Content-Type': 'application/json', 'X-MAG-Token': tok} if data is not None else {}
    req = urllib.request.Request('http://127.0.0.1:%d%s' % (ui, path), data=data,
                                 method='POST' if data is not None else 'GET', headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
     raw = r.read()
     return json.loads(raw) if raw else {}
   body = api('/api/state')
   self.assertEqual([(a['id'], a['slot'], a['model']) for a in body['agents']],
    [('deepseek-chat', 1, 'deepseek-v4-flash-0731'),
     ('qwen-numa1', 2, 'qwen3.8-flash-next-uncensored')])
   # DeepSeek is held in a cold prewarm. Wait until that slot is occupied.
   deadline = time.monotonic() + 6
   while time.monotonic() < deadline:
    if any(p[1] == '0' for p in Model.paths):
     break
    agents = api('/api/state').get('agents') or []
    if any(a.get('id') == 'deepseek-chat' and a.get('prewarm_eta_s') is not None for a in agents):
     break
    time.sleep(0.05)
   else:
    self.fail('DeepSeek never went busy/cold\n%s' % log_path.read_text()[-2000:])
   token = 'hi %d' % time.monotonic_ns()
   t0 = time.monotonic()
   api('/api/message', {'text': token, 'bots': ['deepseek-chat']})
   answered = None
   deadline = t0 + 5
   while time.monotonic() < deadline:
    room = api('/api/state')['room']
    for i, m in enumerate(room):
     if m.get('role') == 'user' and token in (m.get('content') or ''):
      for x in room[i + 1:]:
       if x.get('role') == 'assistant' and (x.get('content') or ''):
        answered = x
        break
    if answered:
     break
    time.sleep(0.05)
   elapsed = time.monotonic() - t0
   self.assertIsNotNone(answered, 'first hi exceeded 5s\n%s' % log_path.read_text()[-2000:])
   self.assertLess(elapsed, 5, 'first hi while DeepSeek is busy')
   self.assertEqual(answered.get('bot'), 'qwen-numa1')
   self.assertFalse(any(m.get('bot') == 'deepseek-chat' and m.get('role') == 'assistant'
                        and (m.get('content') or '') for m in api('/api/state')['room']))
  finally:
   hold.set()
   Model.slot_block = {}
   signal.alarm(0)
   signal.signal(signal.SIGALRM, previous)
   if proc is not None and proc.poll() is None:
    proc.terminate()
    try:
     proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
     proc.kill()
     proc.wait(timeout=3)
   if httpd is not None:
    httpd.shutdown()
   tmp.cleanup()

class SelfTalk(unittest.TestCase):
 """Bots answer each other while Self-Talk is on. No turn cap.

 Stop or the toggle ends the chain. A user message does not: it is inserted
 once, the next free bot answers it, and the chain keeps going. Names in the
 prompt come from the live roster. Idle acknowledgements do not re-arm.
 """
 HARD_S = 28

 def setUp(self):
  import signal
  self._prev = signal.signal(signal.SIGALRM, self._boom)
  signal.alarm(self.HARD_S + 8)
  self.tmp = tempfile.TemporaryDirectory()
  self.proc = None
  self.httpd = None
  self.hold = threading.Event()
  TalkModel.calls = []
  TalkModel.actions = []
  TalkModel.hold = self.hold
  TalkModel.hold_below = 0
  TalkModel.lock = threading.Lock()
  root = pathlib.Path(self.tmp.name)
  self.state = root / 'state'
  self.state.mkdir()
  self.log_path = root / 'server.log'
  self.state.joinpath('agents.json').write_text(json.dumps([
   {'id': 'alpha', 'name': 'Alpha', 'vm': '', 'slot': 1, 'numa': 0,
    'model': 'model-a', 'memory': []},
   {'id': 'beta', 'name': 'Beta', 'vm': '', 'slot': 2, 'numa': 1,
    'model': 'model-b', 'memory': []},
  ]))
  self.state.joinpath('access.json').write_text(json.dumps({
   'owners': ['chris@github'], 'owner_name': 'Chris'}))
  preset = root / 'presets.json'
  preset.write_text(json.dumps([{
   'id': 'test-pair', 'label': 'Test pair',
   'ports': [{'port': 8191, 'numa': 0, 'model': 'model-a'},
             {'port': 8192, 'numa': 1, 'model': 'model-b'}]}]))
  self.preset = preset
  self.httpd = http.server.ThreadingHTTPServer(('127.0.0.1', 0), TalkModel)
  self.model_port = self.httpd.server_address[1]
  threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
  self.ui, self.bridge = port(), port()
  self.env = dict(os.environ,
   MAG_OWNER_CANONICAL_LOGIN='chris@github', MAG_TEST_ALLOW_LOCAL_OWNER='1',
   MAG_ROOM_STATE=str(self.state), MAG_ROOM_PORT=str(self.ui),
   MAG_ROOM_BRIDGE_PORT=str(self.bridge), MAG_ROOM_BRIDGE_IP='127.0.0.1',
   MAG_ROOM_BACKEND='http://127.0.0.1:%d/v1/chat/completions' % self.model_port,
   MAG_ROOM_UI=str(ROOT / 'mag-room/web/index.html'),
   MAG_ROOM_STATIC=str(ROOT / 'mag-room/static'),
   MAG_ROOM_MODEL_STATUS=str(root / 'ms.json'), MAG_NETMODE=str(NETMODE),
   MAG_TEST_NO_MEMGIT='1', MAG_ROOM_AGENT_CTL='/bin/false',
   MAG_ROOM_GUEST_RUN='/bin/false', MAG_ROOM_MCP_AUTO='0', MAG_ROOM_PREWARM='0',
   MAG_ROOM_PROAKTIV_PAUSE_MS='0',
   MAG_ROOM_SINGLE_BOT='', MAG_ROOM_DUAL_NUMA='0', MAG_ROOM_THINKING='off',
   MAG_ROOM_MODEL_ID='model-a', MAG_ROOM_MODEL_LABEL='Pair',
   MAG_ROOM_MODEL_PORTS='8191:0,8192:1', MAG_ROOM_MAX_BOTS='2',
   MAG_ROOM_MODEL_ID_0='model-a', MAG_ROOM_MODEL_LABEL_0='Model A',
   MAG_ROOM_MODEL_ID_1='model-b', MAG_ROOM_MODEL_LABEL_1='Model B',
   MAG_ROOM_PRESETS=str(self.preset))
  self.start_room()

 def _boom(self, signum, frame):
  raise TimeoutError('hard timeout: self-talk test exceeded %ss' % self.HARD_S)

 def start_room(self):
  log = open(self.log_path, 'a')
  try:
   self.proc = subprocess.Popen(
    ['timeout', '-k', '1', '18', str(ROOT / 'build/magroom')],
    env=self.env, stdout=log, stderr=log)
  finally:
   log.close()
  deadline = time.monotonic() + 8
  while time.monotonic() < deadline:
   if self.proc.poll() is not None:
    self.fail('magroom exited %s\n%s' % (self.proc.returncode, self.log_path.read_text()[-2000:]))
   try:
    urllib.request.urlopen('http://127.0.0.1:%d/healthz' % self.ui, timeout=1).read()
    return
   except Exception:
    time.sleep(0.05)
  self.fail('hard timeout waiting for healthz\n%s' % self.log_path.read_text()[-2000:])

 def stop_room(self):
  if self.proc is None or self.proc.poll() is not None:
   return
  self.proc.terminate()
  try:
   self.proc.wait(timeout=3)
  except subprocess.TimeoutExpired:
   self.proc.kill()
   self.proc.wait(timeout=3)

 def tearDown(self):
  import signal
  signal.alarm(0)
  signal.signal(signal.SIGALRM, self._prev)
  self.hold.set()
  self.stop_room()
  if self.httpd is not None:
   self.httpd.shutdown()
  self.tmp.cleanup()

 def token(self):
  html = urllib.request.urlopen('http://127.0.0.1:%d/' % self.ui, timeout=2).read().decode()
  import re
  return re.search(r'name="mag-token" content="([^"]+)"', html).group(1), html

 def api(self, path, body=None, method=None, timeout=2):
  tok, _ = self.token() if not hasattr(self, '_tok') else (self._tok, '')
  if not hasattr(self, '_tok'):
   self._tok = tok
  data = json.dumps(body).encode() if body is not None else None
  headers = {'Content-Type': 'application/json', 'X-MAG-Token': self._tok} if data is not None else {}
  req = urllib.request.Request('http://127.0.0.1:%d%s' % (self.ui, path), data=data,
                               method=method or ('POST' if data is not None else 'GET'), headers=headers)
  with urllib.request.urlopen(req, timeout=timeout) as r:
   raw = r.read()
   return json.loads(raw) if raw else {}

 def chat_calls(self):
  with TalkModel.lock:
   return list(TalkModel.calls)

 def test_off_on_persist_and_names(self):
  _, html = self.token()
  self._tok = re_token(html)
  self.assertIn('id="proaktiv"', html)
  self.assertIn('id="bot-proaktiv"', html)
  self.assertIn('proaktiv-mark', html)
  self.assertIn('proaktiv_jobs', html)
  self.assertIn('>Proaktiv<', html)
  self.assertIn('id="proaktiv-row"', html)
  self.assertNotIn('Self-Talk', html)
  self.assertNotIn('siehe Tooltip', html)
  self.assertIn('state.proaktiv_note', html)
  self.assertIn("$('proaktiv-row')", html)
  self.assertNotIn("$('message').disabled", html)
  self.assertIn('The field itself is never disabled', html)
  self.assertFalse(self.api('/api/state')['selftalk'])
  try:
   self.api('/api/selftalk', {'on': 'yes'})
   self.fail('bad selftalk body was accepted')
  except urllib.error.HTTPError as e:
   self.assertEqual(e.code, 400)
  # Off: the other bot does not react.
  self.api('/api/message', {'text': 'nur-alpha', 'bots': ['alpha']})
  self.wait_calls(1)
  time.sleep(0.4)
  self.assertEqual(len(self.chat_calls()), 1)
  self.assertEqual(self.chat_calls()[0][0], '0')
  # On: a new message arms the chain. Beta answers Alpha by name.
  self.api('/api/selftalk', {'on': True})
  self.assertTrue(self.api('/api/state')['selftalk'])
  self.assertEqual((self.state / 'selftalk').read_text().strip(), 'on')
  self.api('/api/message', {'text': 'zweite', 'bots': ['alpha']})
  self.wait_calls(2)
  beta = [c for c in self.chat_calls() if c[0] == '1']
  self.assertTrue(beta, 'Beta was not given a turn')
  blob = json.dumps(beta[-1][1], ensure_ascii=False)
  self.assertIn('Alpha', blob)
  self.assertIn('FACT-77', blob)
  self.assertIn('Chris', blob)
  self.assertIn('Alpha (model-a)', blob)
  # Rename is live: Beta's next turn uses the new name and Alpha's last text.
  self.api('/api/agents/alpha/name', {'name': 'Gamma'}, method='PUT')
  deadline = time.monotonic() + 5
  seen = None
  while time.monotonic() < deadline:
   for slot, obj in self.chat_calls():
    text = json.dumps(obj, ensure_ascii=False)
    if slot == '1' and 'Gamma (model-a)' in text and 'FACT-77' in text:
     seen = text
     break
   if seen:
    break
   time.sleep(0.05)
  self.assertIsNotNone(seen, 'Beta did not see the renamed bot\n%s' % self.log_path.read_text()[-1500:])
  deadline = time.monotonic() + 4
  while time.monotonic() < deadline:
   room = self.api('/api/state')['room']
   if any(m.get('bot') == 'beta' and m.get('content') == 'sieht Gamma' for m in room):
    break
   time.sleep(0.05)
  else:
   self.fail('Beta did not use the new name: %s' % [m.get('content') for m in self.api('/api/state')['room']])
  marked=[m for m in self.api('/api/state')['room'] if m.get('bot')=='beta' and m.get('selftalk')]
  self.assertTrue(marked, 'proaktiv answer is not marked')
  self.assertTrue(marked[-1].get('cause'))
  self.assertIn('reagiert auf', marked[-1].get('cause_label') or '')
  # The switch survives a restart. The chain does not resume by itself.
  self.stop_room()
  self.hold.set()
  before = len(self.chat_calls())
  self.start_room()
  self._tok = re_token(urllib.request.urlopen('http://127.0.0.1:%d/' % self.ui, timeout=2).read().decode())
  self.assertTrue(self.api('/api/state')['selftalk'])
  self.assertEqual((self.state / 'selftalk').read_text().strip(), 'on')
  time.sleep(0.4)
  self.assertEqual(len(self.chat_calls()), before)
  # Off stops the chain at once and that choice is what a later start reads.
  self.api('/api/selftalk', {'on': False})
  frozen = len(self.chat_calls())
  time.sleep(0.6)
  self.assertEqual(len(self.chat_calls()), frozen)
  self.assertFalse(self.api('/api/state')['selftalk'])
  self.assertEqual((self.state / 'selftalk').read_text().strip(), 'off')

 def test_idle_emoji_selftalk_does_not_publish_or_rearm(self):
  # A legitimate user answer can be followed by an inconsequential
  # LLM reaction. It must not appear in the room or start an endless chain.
  self.api('/api/selftalk',{'on':True})
  self.api('/api/message',{'text':'EMOJI-ONLY','bots':['alpha']})
  deadline=time.monotonic()+6
  while time.monotonic()<deadline:
   room=self.api('/api/state')['room']
   if any(m.get('role')=='assistant' and
          m.get('content')=='FACT-77' for m in room) and len(self.chat_calls())>=3:
    break
   time.sleep(.05)
  time.sleep(.8)
  room=self.api('/api/state')['room']
  self.assertFalse([m for m in room if m.get('role')=='assistant' and
                    '👀' in (m.get('content') or '')],room)
  n=len(self.chat_calls())
  time.sleep(.5)
  self.assertEqual(len(self.chat_calls()),n,'emoji-only reply re-armed self-talk')
  self.api('/api/cancel',{})

 def test_stop_and_toggle_abort_the_chain(self):
  self._tok = re_token(urllib.request.urlopen('http://127.0.0.1:%d/' % self.ui, timeout=2).read().decode())
  TalkModel.hold_below = 2
  self.api('/api/selftalk', {'on': True})
  self.api('/api/message', {'text': 'beide', 'bots': ['alpha', 'beta']})
  self.wait_calls(2)
  n = len(self.chat_calls())
  started = time.monotonic()
  self.api('/api/cancel', {})
  while time.monotonic() - started < 3:
   self.assertEqual(len(self.chat_calls()), n, 'Stop let another bot turn start')
   time.sleep(0.1)
  state = self.api('/api/state')
  self.assertFalse(state['busy'], 'generation was not aborted')
  self.assertTrue(state['selftalk'], 'Stop must leave the switch on')
  self.assertFalse(any(m.get('role') == 'assistant' and m.get('content') for m in state['room']))
  # A new message starts it again.
  self.api('/api/message', {'text': 'nochmal', 'bots': ['alpha']})
  self.wait_calls(n + 1)
  self.assertTrue(any('nochmal' in json.dumps(obj, ensure_ascii=False) for _, obj in self.chat_calls()))
  # Toggle off aborts the same way, once both bots are generating again.
  self.api('/api/cancel', {})
  TalkModel.hold_below = 100
  self.hold.clear()
  before = len(self.chat_calls())
  self.api('/api/selftalk', {'on': True})
  self.api('/api/message', {'text': 'noch-eine', 'bots': ['alpha', 'beta']})
  self.wait_calls(before + 2)
  m = len(self.chat_calls())
  started = time.monotonic()
  self.api('/api/selftalk', {'on': False})
  while time.monotonic() - started < 3:
   self.assertEqual(len(self.chat_calls()), m, 'toggle off let another bot turn start')
   time.sleep(0.1)
  state = self.api('/api/state')
  self.assertFalse(state['busy'])
  self.assertFalse(state['selftalk'])

 def test_chris_can_write_during_selftalk(self):
  self._tok = re_token(urllib.request.urlopen('http://127.0.0.1:%d/' % self.ui, timeout=2).read().decode())
  TalkModel.hold_below = 2
  self.api('/api/selftalk', {'on': True})
  self.api('/api/message', {'text': 'start', 'bots': ['alpha', 'beta']})
  self.wait_calls(2)
  t0 = time.monotonic()
  self.api('/api/message', {'text': 'TOKEN-CHRIS-99', 'bots': ['alpha']})
  self.assertLess(time.monotonic() - t0, 1.0, 'composer send blocked while bots were talking')
  state = self.api('/api/state')
  users = [m for m in state['room'] if m.get('content') == 'TOKEN-CHRIS-99' and m.get('role') == 'user']
  self.assertEqual(len(users), 1)
  self.assertEqual(users[0].get('name'), 'Chris')
  pending = state.get('pending') or []
  self.assertFalse(any(p.get('text') == 'TOKEN-CHRIS-99' for p in pending))
  self.hold.set()
  deadline = time.monotonic() + 5
  referred = False
  while time.monotonic() < deadline:
   room = self.api('/api/state')['room']
   if any(m.get('role') == 'assistant' and 'TOKEN-CHRIS-99' in (m.get('content') or '') for m in room):
    referred = True
    break
   time.sleep(0.05)
  self.assertTrue(referred, 'next bot answer did not take Chris into account')
  users = [m for m in self.api('/api/state')['room'] if m.get('content') == 'TOKEN-CHRIS-99' and m.get('role') == 'user']
  self.assertEqual(len(users), 1, 'Chris message was duplicated')
  mark = len(self.chat_calls())
  deadline = time.monotonic() + 3
  while time.monotonic() < deadline and len(self.chat_calls()) <= mark:
   time.sleep(0.05)
  self.assertGreater(len(self.chat_calls()), mark, 'self-talk stopped after Chris wrote')
  self.assertTrue(self.api('/api/state')['selftalk'])
  self.api('/api/cancel', {})

 def test_deploy_hold_pauses_at_turn_boundary_and_resumes(self):
  self._tok = re_token(urllib.request.urlopen('http://127.0.0.1:%d/' % self.ui, timeout=2).read().decode())
  self.hold.set()
  self.api('/api/selftalk', {'on': True})
  self.api('/api/message', {'text': 'kette', 'bots': ['alpha']})
  self.wait_calls(2)
  held = self.api('/api/deploy-hold', {'on': True})
  self.assertTrue(held['selftalk'])
  self.assertTrue(held['held'])
  deadline = time.monotonic() + 4
  while time.monotonic() < deadline:
   st = self.api('/api/state')
   if not st.get('busy') and not st.get('selftalk_busy'):
    break
   time.sleep(0.05)
  else:
   self.fail('Self-Talk did not stop at a turn boundary: %s' % self.api('/api/state'))
  frozen = len(self.chat_calls())
  time.sleep(0.4)
  self.assertEqual(len(self.chat_calls()), frozen, 'Self-Talk started another turn after deploy-hold')
  st = self.api('/api/state')
  self.assertTrue(st['selftalk'])
  self.assertTrue(st.get('selftalk_held'))
  self.assertFalse(st.get('user_busy'))
  self.assertEqual((self.state / 'selftalk').read_text().strip(), 'on')
  self.assertEqual((self.state / 'selftalk-resume').read_text().strip(), 'on')
  self.stop_room()
  before = len(self.chat_calls())
  self.start_room()
  self._tok = re_token(urllib.request.urlopen('http://127.0.0.1:%d/' % self.ui, timeout=2).read().decode())
  st = self.api('/api/state')
  self.assertTrue(st['selftalk'])
  self.assertTrue(st.get('selftalk_held'), st)
  time.sleep(0.35)
  self.assertEqual(len(self.chat_calls()), before, 'Proaktiv resumed before the warm-hi gate')
  released = self.api('/api/deploy-hold', {'on': False})
  self.assertFalse(released.get('held'))
  deadline = time.monotonic() + 5
  while time.monotonic() < deadline:
   if len(self.chat_calls()) > before:
    break
   time.sleep(0.05)
  else:
   self.fail('Self-Talk did not resume after the gate released the hold\n%s' % self.log_path.read_text()[-1500:])
  self.api('/api/cancel', {})

 def test_idle_echo_does_not_rearm_selftalk(self):
  self._tok = re_token(urllib.request.urlopen('http://127.0.0.1:%d/' % self.ui, timeout=2).read().decode())
  self.hold.set()
  self.api('/api/selftalk', {'on': True})
  self.api('/api/message', {'text': 'IDLE-ECHO', 'bots': ['alpha']})
  self.wait_calls(1)
  frozen = len(self.chat_calls())
  time.sleep(0.6)
  self.assertEqual(len(self.chat_calls()), frozen, 'idle acknowledgement re-armed Self-Talk')
  st = self.api('/api/state')
  self.assertTrue(st['selftalk'], 'idle damp must leave the switch on')
  self.assertFalse(st.get('selftalk_busy'))
  self.assertFalse(st['busy'])
  room = [m.get('content') for m in st['room'] if m.get('role') == 'assistant']
  self.assertTrue(any('Kein Auftrag' in (c or '') or 'bleibe still' in (c or '') for c in room), room)
  self.api('/api/message', {'text': 'TOKEN-CHRIS-99', 'bots': ['beta']})
  deadline = time.monotonic() + 5
  while time.monotonic() < deadline:
   if any(m.get('role') == 'assistant' and 'TOKEN-CHRIS-99' in (m.get('content') or '')
          for m in self.api('/api/state')['room']):
    break
   time.sleep(0.05)
  else:
   self.fail('real user text after idle echo did not get an answer')
  self.assertTrue(self.api('/api/state')['selftalk'])
  self.api('/api/cancel', {})

 def test_deploy_hold_drains_user_reply(self):
  self._tok = re_token(urllib.request.urlopen('http://127.0.0.1:%d/' % self.ui, timeout=2).read().decode())
  TalkModel.hold_below = 1
  self.hold.clear()
  self.api('/api/selftalk', {'on': True})
  self.api('/api/message', {'text': 'user-reply', 'bots': ['alpha']})
  self.wait_calls(1)
  st = self.api('/api/state')
  self.assertTrue(st['busy'])
  self.assertTrue(st.get('user_busy'), st)
  self.api('/api/deploy-hold', {'on': True})
  st = self.api('/api/state')
  self.assertTrue(st['selftalk'], 'deploy-hold must leave the switch on')
  self.assertTrue(st.get('user_busy') or st['busy'], 'user reply was cancelled')
  self.hold.set()
  deadline = time.monotonic() + 4
  while time.monotonic() < deadline:
   st = self.api('/api/state')
   if not st.get('busy') and not st.get('user_busy'):
    break
   time.sleep(0.05)
  else:
   self.fail('user reply did not drain: %s' % self.api('/api/state'))
  frozen = len(self.chat_calls())
  time.sleep(0.4)
  self.assertEqual(len(self.chat_calls()), frozen, 'Self-Talk continued after the user reply drained')
  self.assertTrue(self.api('/api/state')['selftalk'])

 def test_proaktiv_migrates_selftalk_file(self):
  self._tok = re_token(urllib.request.urlopen('http://127.0.0.1:%d/' % self.ui, timeout=2).read().decode())
  self.stop_room()
  (self.state / 'selftalk').write_text('on')
  pro = self.state / 'proaktiv'
  if pro.exists():
   pro.unlink()
  self.start_room()
  self._tok = re_token(urllib.request.urlopen('http://127.0.0.1:%d/' % self.ui, timeout=2).read().decode())
  st = self.api('/api/state')
  self.assertTrue(st['proaktiv'])
  self.assertTrue(st['selftalk'])
  self.assertEqual((self.state / 'proaktiv').read_text().strip(), 'on')
  self.assertEqual((self.state / 'selftalk').read_text().strip(), 'on')

 def test_disabled_bot_stays_off_across_proaktiv_and_restart(self):
  self._tok = re_token(urllib.request.urlopen('http://127.0.0.1:%d/' % self.ui, timeout=2).read().decode())
  self.hold.set()
  self.api('/api/bot-settings', {'bots': ['beta'], 'enabled': False}, method='PUT')
  self.api('/api/proaktiv', {'on': True})
  st = self.api('/api/state')
  beta = next(a for a in st['agents'] if a['id'] == 'beta')
  self.assertIs(beta['enabled'], False)
  self.assertIn('nur 1 Bot aktiv', st.get('proaktiv_note') or '')
  before = len(self.chat_calls())
  self.api('/api/message', {'text': 'nur-user', 'bots': ['alpha', 'beta']})
  self.wait_calls(before + 1)
  time.sleep(0.5)
  slots = [c[0] for c in self.chat_calls()[before:]]
  self.assertIn('0', slots)
  self.assertNotIn('1', slots, 'disabled beta must not answer')
  self.api('/api/proaktiv', {'on': False})
  self.api('/api/proaktiv', {'on': True})
  beta = next(a for a in self.api('/api/state')['agents'] if a['id'] == 'beta')
  self.assertIs(beta['enabled'], False)
  self.stop_room()
  self.start_room()
  self._tok = re_token(urllib.request.urlopen('http://127.0.0.1:%d/' % self.ui, timeout=2).read().decode())
  st = self.api('/api/state')
  beta = next(a for a in st['agents'] if a['id'] == 'beta')
  self.assertIs(beta['enabled'], False)
  self.assertTrue(st['proaktiv'])
  frozen = len(self.chat_calls())
  self.api('/api/message', {'text': 'nach-restart', 'bots': ['alpha', 'beta']})
  self.wait_calls(frozen + 1)
  time.sleep(0.4)
  self.assertNotIn('1', [c[0] for c in self.chat_calls()[frozen:]])

 def test_human_hi_preempts_bot_to_bot_and_both_answer(self):
  # A running bot-to-bot turn must not keep the only slot. Chris's hi cancels
  # it, restores the prefix, and both bots accept the new prompt within 5s.
  self.stop_room()
  self.env['MAG_ROOM_PROAKTIV_PAUSE_MS'] = '1200'
  TalkModel.calls = []
  TalkModel.actions = []
  TalkModel.hold_below = 0
  self.hold.set()
  self.start_room()
  self._tok = re_token(urllib.request.urlopen('http://127.0.0.1:%d/' % self.ui, timeout=2).read().decode())
  self.api('/api/proaktiv', {'on': True})
  self.api('/api/message', {'text': 'seed-chain', 'bots': ['alpha']})
  deadline = time.monotonic() + 6
  while time.monotonic() < deadline:
   calls = self.chat_calls()
   st = self.api('/api/state')
   short = [c for c in calls if (c[1] or {}).get('max_tokens') == 256]
   if short and not st.get('busy') and (self.state / 'prompt-prefix.json').is_file():
    break
   time.sleep(0.05)
  else:
   self.fail('bot-to-bot turn did not settle\n%s' % self.log_path.read_text()[-1500:])
  n = len(self.chat_calls())
  TalkModel.hold_below = n + 2
  self.hold.clear()
  self.wait_calls(n + 2)
  held = self.chat_calls()[n:n + 2]
  self.assertEqual({c[0] for c in held}, {'0', '1'}, held)
  for _slot, obj in held:
   self.assertEqual(obj.get('max_tokens'), 256, obj)
   blob = json.dumps(obj, ensure_ascii=False)
   self.assertLess(len(blob), 8000, 'bot-to-bot prompt was not shortened')
   self.assertNotIn('"tools"', blob)
  t0 = time.monotonic()
  self.api('/api/message', {'text': 'HI-CHRIS-7', 'bots': ['alpha', 'beta']}, timeout=3)
  seen = {}
  while time.monotonic() - t0 < 5:
   for slot, obj in self.chat_calls()[n + 2:]:
    if 'HI-CHRIS-7' in json.dumps(obj, ensure_ascii=False):
     seen[slot] = obj
   if '0' in seen and '1' in seen:
    break
   time.sleep(0.02)
  self.assertLess(time.monotonic() - t0, 5, 'both bots did not take the hi within 5s: %s' % list(seen))
  self.assertEqual(set(seen), {'0', '1'})
  for obj in seen.values():
   self.assertGreater(obj.get('max_tokens') or 0, 256)
  with TalkModel.lock:
   actions = list(TalkModel.actions)
  cancel_slots = {slot for path, slot in actions if 'action=cancel' in path}
  self.assertTrue({'0', '1'} <= cancel_slots, actions)
  self.assertTrue(any('action=restore' in path for path, _slot in actions), actions)
  self.hold.set()
  mark = len(self.chat_calls())
  time.sleep(0.35)
  self.assertEqual(len(self.chat_calls()), mark, 'proaktiv took the slot during the pause')
  deadline = time.monotonic() + 3
  while time.monotonic() < deadline and len(self.chat_calls()) == mark:
   time.sleep(0.05)
  self.assertGreater(len(self.chat_calls()), mark, 'proaktiv ended after one pause')
  self.assertTrue(self.api('/api/state')['proaktiv'])
  self.api('/api/cancel', {})

 def test_user_and_bot_messages_wake_the_other_active_bot(self):
  self._tok = re_token(urllib.request.urlopen('http://127.0.0.1:%d/' % self.ui, timeout=2).read().decode())
  self.hold.set()
  self.api('/api/proaktiv', {'on': True})
  self.api('/api/message', {'text': 'von-chris', 'bots': ['alpha']})
  deadline = time.monotonic() + 6
  user_woke = bot_woke = False
  while time.monotonic() < deadline and not (user_woke and bot_woke):
   for slot, obj in self.chat_calls():
    blob = json.dumps(obj, ensure_ascii=False)
    if slot == '1' and 'von-chris' in blob:
     user_woke = True
    if slot == '0' and '[Beta (' in blob and 'FACT-77' in blob:
     bot_woke = True
   time.sleep(0.05)
  self.assertTrue(user_woke, 'user message did not wake the other active bot')
  self.assertTrue(bot_woke, 'bot message did not wake the other active bot')
  self.api('/api/cancel', {})

 def test_stop_cancels_both_slots_reloads_prefix_and_next_hi_runs(self):
  self._tok = re_token(urllib.request.urlopen('http://127.0.0.1:%d/' % self.ui, timeout=2).read().decode())
  self.hold.set()
  self.api('/api/message', {'text': 'warm', 'bots': ['alpha', 'beta']})
  self.wait_calls(2)
  deadline = time.monotonic() + 4
  while time.monotonic() < deadline:
   st = self.api('/api/state')
   if not st.get('busy') and (self.state / 'prompt-prefix.json').exists():
    break
   time.sleep(0.05)
  else:
   self.fail('prefix was not saved\n%s' % self.log_path.read_text()[-1500:])
  self.hold.clear()
  TalkModel.hold_below = 100
  self.api('/api/message', {'text': 'beide-laufen', 'bots': ['alpha', 'beta']})
  self.wait_calls(4)
  self.api('/api/message', {'text': 'Soll-weg', 'bots': ['alpha']})
  t0 = time.monotonic()
  cancelled = self.api('/api/cancel', {})
  self.assertTrue(cancelled['cancelled'])
  st = self.api('/api/state')
  self.assertFalse(st['busy'])
  self.assertEqual(st.get('pending') or [], [])
  self.assertEqual(st.get('busy_bots') or [], [])
  with TalkModel.lock:
   actions = list(TalkModel.actions)
  slots = {slot for path, slot in actions}
  kinds = {path for path, _slot in actions}
  self.assertTrue(any('action=cancel' in path for path in kinds), actions)
  self.assertTrue(any('action=restore' in path for path in kinds), actions)
  self.assertIn('0', slots)
  self.assertIn('1', slots)
  self.hold.set()
  TalkModel.hold_below = 0
  before = len(self.chat_calls())
  self.api('/api/message', {'text': 'hi', 'bots': ['alpha']})
  self.wait_calls(before + 1)
  self.assertLess(time.monotonic() - t0, 3, 'next hi did not start promptly')

 def wait_calls(self, n):
  deadline = time.monotonic() + 6
  while time.monotonic() < deadline:
   if len(self.chat_calls()) >= n:
    return
   if self.proc.poll() is not None:
    self.fail('magroom exited\n%s' % self.log_path.read_text()[-2000:])
   time.sleep(0.05)
  self.fail('wanted %d model calls, have %d\n%s' % (n, len(self.chat_calls()), self.log_path.read_text()[-2000:]))

def re_token(html):
 import re
 return re.search(r'name="mag-token" content="([^"]+)"', html).group(1)

class TalkModel(http.server.BaseHTTPRequestHandler):
 calls = []
 actions = []
 hold = None
 hold_below = 0
 lock = threading.Lock()
 protocol_version = 'HTTP/1.1'
 def log_message(self, *a):
  pass
 def do_GET(self):
  data = b'{"status":"ok"}'
  self.send_response(200)
  self.send_header('Content-Length', str(len(data)))
  self.end_headers()
  self.wfile.write(data)
 def do_POST(self):
  raw = self.rfile.read(int(self.headers.get('Content-Length', '0')))
  if self.path.startswith('/slots/'):
   with type(self).lock:
    type(self).actions.append((self.path, self.headers.get('X-Mag-Slot')))
  if self.path != '/v1/chat/completions':
   data = b'{"tokens":[1]}'
   self.send_response(200)
   self.send_header('Content-Length', str(len(data)))
   self.end_headers()
   self.wfile.write(data)
   return
  obj = json.loads(raw)
  with type(self).lock:
   type(self).calls.append((self.headers.get('X-Mag-Slot'), obj))
   n = len(type(self).calls)
  blob = json.dumps(obj, ensure_ascii=False)
  if n <= type(self).hold_below and type(self).hold is not None:
   type(self).hold.wait(12)
  if 'Gamma (model-a)' in blob:
   text = 'sieht Gamma'
  elif 'TOKEN-CHRIS-99' in blob:
   text = 'zu Chris: TOKEN-CHRIS-99'
  elif 'IDLE-ECHO' in blob:
   text = 'Kein Auftrag. Ich bleibe still.'
  elif 'EMOJI-ONLY' in blob and 'Antworte in höchstens zwei kurzen Sätzen' in blob:
   text = '[Beta] 👀'
  else:
   text = 'FACT-77'
  try:
   self.send_response(200)
   self.send_header('Transfer-Encoding', 'chunked')
   self.end_headers()
   event = json.dumps({'choices': [{'delta': {'content': text}}]}).encode()
   payload = b'data: ' + event + b'\n\n'
   self.wfile.write(('%x\r\n' % len(payload)).encode() + payload + b'\r\n')
   end = b'data: [DONE]\n\n'
   self.wfile.write(('%x\r\n' % len(end)).encode() + end + b'\r\n0\r\n\r\n')
   self.wfile.flush()
  except (BrokenPipeError, ConnectionResetError):
   pass


def _fnv_hex(s):
 h=14695981039346656037
 for c in s.encode():
  h^=c
  h=(h*1099511628211)&0xFFFFFFFFFFFFFFFF
 return '%016x'%h

class TrailingAssistant(unittest.TestCase):
 """Proaktiv on, two DeepSeek jobs and no new user line.

 The stored wire already ends on the bot's own assistant reply. Neither
 request may be sent that way, the room must not show the thinking-prefill
 500, and the prefix messages stay.
 """
 @classmethod
 def setUpClass(cls):
  cls.tmp=tempfile.TemporaryDirectory();cls.root=pathlib.Path(cls.tmp.name)
  cls.state=cls.root/'state';cls.state.mkdir()
  cls.state.joinpath('agents.json').write_text(json.dumps([
   {'id':'deepseek-chat','name':'Alpha','vm':'','slot':1,'numa':0,
    'model':'deepseek-v4-flash-0731','memory':[]},
   {'id':'lehotbot','name':'Beta','vm':'','slot':2,'numa':1,
    'model':'deepseek-v4-flash-0731','memory':[]}]))
  cls.state.joinpath('room.json').write_text(json.dumps([
   {'id':'a1','role':'assistant','name':'Alpha','bot':'deepseek-chat','content':'alte antwort'}]))
  cls.state.joinpath('proaktiv').write_text('on')
  cls.state.joinpath('selftalk').write_text('on')
  company='\nRaum: Beta (deepseek-v4-flash-0731). Sprich die anderen mit Namen an.'
  wire={'fingerprint':'kept','memory':'[Erinnerungen]','plugins':'','files':'(keine)',
        'company':company,'skills':'','seen':['a1'],
        'messages':[{'role':'system','content':'PREFIX-KEEP'},
                    {'role':'user','content':'alte frage'},
                    {'role':'assistant','content':'alte antwort'}]}
  cls.wire_path=cls.state/('prompt-wire-'+_fnv_hex('deepseek-chat')+'.json')
  cls.wire_path.write_text(json.dumps(wire))
  cls.before_messages=wire['messages']
  cls.state.joinpath('queue.json').write_text(json.dumps({
   'revision':2,
   'jobs':[{'ids':['deepseek-chat'],'think':1,'input':''},
           {'ids':['deepseek-chat'],'think':1,'input':''}]}))
  cls.model=http.server.ThreadingHTTPServer(('127.0.0.1',0),Model)
  threading.Thread(target=cls.model.serve_forever,daemon=True).start()
  cls.ui,cls.bridge=port(),port()
  cls.env=dict(os.environ,MAG_OWNER_CANONICAL_LOGIN='chris@github',MAG_TEST_ALLOW_LOCAL_OWNER='1',
    MAG_ROOM_STATE=str(cls.state),MAG_ROOM_PORT=str(cls.ui),MAG_ROOM_BRIDGE_PORT=str(cls.bridge),
    MAG_ROOM_BRIDGE_IP='127.0.0.1',MAG_ROOM_BACKEND=f'http://127.0.0.1:{cls.model.server_port}/v1/chat/completions',
    MAG_ROOM_UI=str(ROOT/'mag-room/web/index.html'),MAG_ROOM_STATIC=str(ROOT/'mag-room/static'),
    MAG_ROOM_MODEL_STATUS=str(cls.root/'ms.json'),MAG_NETMODE=str(NETMODE),
    MAG_TEST_NO_MEMGIT='1',MAG_ROOM_AGENT_CTL='/bin/false',MAG_ROOM_GUEST_RUN='/bin/false',
    MAG_ROOM_MCP_AUTO='0',MAG_ROOM_PREWARM='0',MAG_ROOM_PROAKTIV_PAUSE_MS='0',
    MAG_ROOM_SINGLE_BOT='',MAG_ROOM_DUAL_NUMA='1',MAG_ROOM_THINKING='on',
    MAG_ROOM_MODEL_ID='deepseek-v4-flash-0731',MAG_ROOM_MODEL_LABEL='DeepSeek V4 Flash',
    MAG_ROOM_MODEL_PORTS='8191:0,8192:1',MAG_ROOM_MAX_BOTS='2',
    MAG_ROOM_PRESETS=str(ROOT/'ops/room/presets.json'))
  cls.log_path=cls.root/'server.log'
  cls.paths_at=len(Model.paths)
  log=open(cls.log_path,'w')
  cls.proc=subprocess.Popen([str(ROOT/'build/magroom')],env=cls.env,stdout=log,stderr=log)
  log.close()
  wait_http(f'http://127.0.0.1:{cls.ui}/healthz',timeout=8,proc=cls.proc)
 @classmethod
 def tearDownClass(cls):
  if cls.proc.poll() is None:
   cls.proc.terminate()
   try:cls.proc.wait(timeout=3)
   except Exception:cls.proc.kill()
  cls.model.shutdown();cls.tmp.cleanup()
 def request(self,path,body=None,method=None):
  data=json.dumps(body).encode() if body is not None else None
  headers={'Content-Type':'application/json'} if data is not None else {}
  m=(method or ('POST' if data is not None else 'GET')).upper()
  if m not in ('GET','HEAD'):headers['X-MAG-Token']=_ui_token(self.ui)
  req=urllib.request.Request(f'http://127.0.0.1:{self.ui}{path}',data=data,method=method or ('POST' if data is not None else 'GET'),headers=headers)
  with urllib.request.urlopen(req,timeout=3) as r:
   raw=r.read()
   return json.loads(raw) if raw else {}
 def test_two_jobs_do_not_end_on_assistant(self):
  deadline=time.monotonic()+6
  while time.monotonic()<deadline:
   log=self.log_path.read_text(errors='replace')
   if log.count('skip-trailing-assistant bot=deepseek-chat')>=2:break
   time.sleep(0.05)
  else:
   self.fail('expected two skips\n%s'%self.log_path.read_text(errors='replace')[-2000:])
  chats=[]
  for entry in Model.paths[self.paths_at:]:
   if not (isinstance(entry,tuple) and len(entry)>=4 and isinstance(entry[3],dict)):continue
   if not str(entry[0]).startswith('/v1/chat'):continue
   chats.append(entry[3])
  for call in chats:
   msgs=call.get('messages') or []
   self.assertTrue(msgs)
   self.assertNotEqual(msgs[-1].get('role'),'assistant',call)
   self.assertFalse(call.get('chat_template_kwargs',{}).get('enable_thinking') and msgs[-1].get('role')=='assistant')
  room=self.request('/api/state')['room']
  blob=json.dumps(room,ensure_ascii=False)
  self.assertNotIn('incompatible with enable_thinking',blob)
  self.assertNotIn('HTTP 500',blob)
  self.assertNotIn('Antwort fehlgeschlagen',blob)
  self.assertIn('alte antwort',blob)
  saved=json.loads(self.wire_path.read_text())
  self.assertEqual(saved.get('messages'),self.before_messages)
  html=urllib.request.urlopen(f'http://127.0.0.1:{self.ui}/',timeout=2).read().decode()
  self.assertIn('id="bot-proaktiv"',html)
  self.assertIn('id="bot-active"',html)
  agents={a['id']:a for a in self.request('/api/state')['agents']}
  self.assertIs(agents['deepseek-chat']['proaktiv'],True)
  self.assertIsNot(agents['deepseek-chat']['enabled'],False)
  self.request('/api/bot-settings',{'bots':['deepseek-chat'],'proaktiv':False},method='PUT')
  agents={a['id']:a for a in self.request('/api/state')['agents']}
  self.assertIs(agents['deepseek-chat']['proaktiv'],False)
  self.assertIsNot(agents['deepseek-chat']['enabled'],False)

if __name__=='__main__':unittest.main(verbosity=2)

