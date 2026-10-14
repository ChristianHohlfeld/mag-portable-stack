"""Isolated native integration checks. Uses temporary state and a fake model."""
import http.server,json,os,pathlib,socket,subprocess,tempfile,threading,time,unittest,urllib.request,urllib.error
ROOT=pathlib.Path(__file__).resolve().parents[2]
def port():
 with socket.socket() as s:s.bind(('127.0.0.1',0));return s.getsockname()[1]
class Model(http.server.BaseHTTPRequestHandler):
 protocol_version='HTTP/1.1';calls=[];delay=0;reply=None;script=[]
 def log_message(self,*a):pass
 def do_GET(self):
  data=b'{"data":[{"id":"gpt-oss:120b"}]}'
  self.send_response(200);self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
 def do_POST(self):
  obj=json.loads(self.rfile.read(int(self.headers['Content-Length'])));self.calls.append(obj)
  self.send_response(200);self.send_header('Transfer-Encoding','chunked');self.end_headers()
  if self.delay:time.sleep(self.delay)
  try:
   scripted=type(self).script.pop(0) if type(self).script else None
   chunks=scripted if scripted is not None else ([type(self).reply] if type(self).reply is not None else ['Hallo ','aus dem ','Modell.'])
   for piece in chunks:
    delta=piece if isinstance(piece,dict) else {'content':piece}
    event=json.dumps({'choices':[{'delta':delta}]}).encode()
    for payload in [b'data: '+event+b'\n\n',b'']:
     if payload:self.wfile.write(('%x\r\n'%len(payload)).encode()+payload+b'\r\n');self.wfile.flush()
   end=b'data: [DONE]\n\n';self.wfile.write(('%x\r\n'%len(end)).encode()+end+b'\r\n0\r\n\r\n');self.wfile.flush()
  except (BrokenPipeError,ConnectionResetError):pass
class Native(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.tmp=tempfile.TemporaryDirectory();cls.state=pathlib.Path(cls.tmp.name)/'state';cls.state.mkdir()
  cls.state.joinpath('agents.json').write_text(json.dumps([{'id':'test','name':'Test','vm':'','memory':[]}]))
  cls.model=http.server.ThreadingHTTPServer(('127.0.0.1',0),Model);threading.Thread(target=cls.model.serve_forever,daemon=True).start();cls.ui=port();cls.bridge=port()
  cls.start()
 @classmethod
 def start(cls):
  env=dict(os.environ,MAG_ROOM_STATE=str(cls.state),MAG_ROOM_PORT=str(cls.ui),MAG_ROOM_BRIDGE_PORT=str(cls.bridge),MAG_ROOM_BRIDGE_IP='127.0.0.1',MAG_ROOM_BACKEND=f'http://127.0.0.1:{cls.model.server_port}/v1/chat/completions',MAG_ROOM_UI=str(ROOT/'mag-room/web/index.html'),MAG_ROOM_STATIC=str(ROOT/'mag-room/static'),MAG_ROOM_AUTOTALK_SLEEP='1')
  cls.log=open(pathlib.Path(cls.tmp.name)/'server.log','a');cls.proc=subprocess.Popen([str(ROOT/'build/magroom')],env=env,stdout=cls.log,stderr=cls.log)
  for _ in range(100):
   try:cls.request('/healthz',raw=True);return
   except Exception:time.sleep(.03)
  raise RuntimeError('Native startup failed')
 @classmethod
 def tearDownClass(cls):
  cls.proc.terminate();cls.proc.wait(timeout=5);cls.log.close();cls.model.shutdown();cls.tmp.cleanup()
 @classmethod
 def request(cls,path,body=None,method=None,raw=False,bridge=False,headers=None):
  data=body if isinstance(body,bytes) else json.dumps(body).encode() if body is not None else None
  req=urllib.request.Request(f'http://127.0.0.1:{cls.bridge if bridge else cls.ui}'+path,data=data,method=method,headers=headers or ({'Content-Type':'application/json'} if body is not None else {}))
  with urllib.request.urlopen(req,timeout=10) as r:
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
  self.assertIn(b'Ein Raum',self.request('/',raw=True));self.assertIn(b'function',self.request('/static/marked.min.js',raw=True))
 def test_02_chunked_model_and_selected_agents(self):
  self.request('/api/message',{'text':'Test','bots':['test'],'web_search':False})
  for _ in range(100):
   s=self.request('/api/state')
   if any(m.get('content')=='Hallo aus dem Modell.' for m in s['room']):break
   time.sleep(.05)
  else:self.fail('Chunked model response lost')
  self.assertEqual(Model.calls[-1]['model'],'gpt-oss:120b');self.assertFalse(s['web']);self.assertTrue(Model.calls[-1]['stream'])
 def test_03_upload_reset_preserves_memory(self):
  self.request('/api/agent/test/remember',{'fact':'Merke mich'})
  f=self.request('/api/upload?name=hello.txt',b'hello')
  self.assertEqual(self.request('/api/file/'+f['stored'],raw=True),b'hello')
  self.request('/api/reset',{})
  self.assertFalse(self.request('/api/state')['files']);self.assertEqual(self.request('/api/agents/test/memory')[0]['text'],'Merke mich')
  self.assertFalse(list(self.state.joinpath('files').iterdir()));self.error(404,'/api/file/'+f['stored'])
 def test_04_bridge_auth_and_routes(self):
  self.error(401,'/api/state',bridge=True)
  token=self.state.joinpath('token').read_text().strip();h={'Authorization':'Bearer '+token}
  self.assertEqual(self.request('/api/state',bridge=True,headers=h)['model'],'gpt-oss:120b')
  self.error(403,'/api/stop',{},bridge=True,headers=h)
 def test_05_validation_origin_and_traversal(self):
  self.error(400,'/api/message',{'text':''});self.error(400,'/api/autotalk',{'on':'yes'})
  self.error(403,'/api/stop',{},headers={'Origin':'https://evil.example'})
  self.error(404,'/static/%2e%2e%2fagents.json');self.error(404,'/api/file/../../etc/passwd')
 def test_06_stop_interrupts_and_human_input_stays_responsive(self):
  Model.delay=3;self.request('/api/autotalk',{'on':True})
  time.sleep(.15)
  self.assertFalse(self.request('/api/state')['busy'],'Autonomy must be idle without a new task')
  start=time.monotonic();self.request('/api/message',{'text':'Ich bin auch hier','bots':['test']});self.assertLess(time.monotonic()-start,.5)
  for _ in range(100):
   if self.request('/api/state')['busy']:break
   time.sleep(.02)
  self.assertTrue(self.request('/api/state')['busy'])
  start=time.monotonic();self.request('/api/stop',{});s=self.wait_idle();self.assertLess(time.monotonic()-start,1)
  self.assertFalse(s['autotalk']);Model.delay=0
 def test_07_persistence(self):
  before=self.request('/api/state');self.proc.terminate();self.proc.wait(timeout=5);self.log.close();self.start();after=self.request('/api/state')
  self.assertEqual(before['room'],after['room']);self.assertEqual(before['agents'],after['agents']);self.assertFalse(after['autotalk'])
 def test_08_idle_autonomy_does_not_make_up_work(self):
  self.request('/api/autotalk',{'on':True})
  calls_before=len(Model.calls)
  time.sleep(1.2)
  self.assertEqual(len(Model.calls),calls_before,'Autonomy should not run without new user work')
  self.assertTrue(self.request('/api/state')['autotalk'],'Keep the enabled switch ON')
  self.request('/api/stop',{})
 def test_09_task_without_tools_stops_after_one_answer(self):
  Model.reply='Erledigt. [[MAG:WEITER]]'
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
   self.request('/api/stop',{});Model.reply=None
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
   else:self.fail('Verified tool action did not yield one controlled continuation')
   self.assertEqual(len(Model.calls),initial+3)
   self.assertFalse(any('[[MAG:' in x for x in bot_messages),'Do not persist internal markers')
   time.sleep(1.2)
   self.assertEqual(len(Model.calls),initial+3,'Final status must not trigger another bot turn')
   self.assertTrue(state['autotalk'],'Autonomy may remain enabled while idle')
  finally:
   self.request('/api/stop',{});Model.script=[]
 def test_11_malformed_http_rejected(self):
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

if __name__=='__main__':unittest.main(verbosity=2)
