"""Isolated native integration checks. Uses temporary state and a fake model."""
import http.server,json,os,pathlib,socket,subprocess,tempfile,threading,time,unittest,urllib.request,urllib.error
ROOT=pathlib.Path(__file__).resolve().parents[2]
def port():
 with socket.socket() as s:s.bind(('127.0.0.1',0));return s.getsockname()[1]
class Model(http.server.BaseHTTPRequestHandler):
 protocol_version='HTTP/1.1';calls=[];delay=0;reply=None;script=[];health=200
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
  self.send_response(200);self.send_header('Transfer-Encoding','chunked');self.end_headers()
  if self.delay:time.sleep(self.delay)
  try:
   scripted=type(self).script.pop(0) if type(self).script else None
   chunks=scripted if scripted is not None else ([type(self).reply] if type(self).reply is not None else ['Hallo ','aus dem ','Modell.'])
   for piece in chunks:
    if isinstance(piece,dict) and '_chunk' in piece:event=json.dumps(piece['_chunk']).encode()
    else:
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
  env=dict(os.environ,MAG_ROOM_STATE=str(cls.state),MAG_ROOM_PORT=str(cls.ui),MAG_ROOM_BRIDGE_PORT=str(cls.bridge),MAG_ROOM_BRIDGE_IP='127.0.0.1',MAG_ROOM_BACKEND=f'http://127.0.0.1:{cls.model.server_port}/v1/chat/completions',MAG_ROOM_UI=str(ROOT/'mag-room/web/index.html'),MAG_ROOM_STATIC=str(ROOT/'mag-room/static'),MAG_ROOM_AUTOTALK_SLEEP='1',MAG_ROOM_MODEL_STATUS=str(pathlib.Path(cls.tmp.name)/'model-status.json'))
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
  self.assertIn(b'Privater Raum',self.request('/',raw=True));self.assertIn(b'function',self.request('/static/marked.min.js',raw=True))
 def test_02_chunked_model_and_selected_agents(self):
  self.request('/api/message',{'text':'Test','bots':['test'],'web_search':False})
  for _ in range(100):
   s=self.request('/api/state')
   if any(m.get('content')=='Hallo aus dem Modell.' for m in s['room']):break
   time.sleep(.05)
  else:self.fail('Chunked model response lost')
  self.assertEqual(Model.calls[-1]['model'],'deepseek-v4-flash-0731');self.assertFalse(s['web']);self.assertTrue(Model.calls[-1]['stream'])
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
  self.assertEqual(self.request('/api/state',bridge=True,headers=h)['model'],'deepseek-v4-flash-0731')
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
 def test_09b_reasoning_is_returned_with_tool_history(self):
  first=[{'reasoning_content':'Ich merke mir das.'},{'tool_calls':[{'index':0,'id':'call_ds_1','function':{'name':'remember','arguments':'{"fact":"DeepSeek-Test"}'}}]}]
  Model.script=[first,['Notiert.']]
  try:
   initial=len(Model.calls)
   self.request('/api/message',{'text':'Merke DeepSeek-Test','bots':['test']})
   self.wait_room('Notiert.');self.wait_idle()
   self.assertEqual(len(Model.calls),initial+2)
   msgs=Model.calls[-1]['messages']
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
                 "if(!a.vm)continue","if(!bot.vm){","Zeichen Überlegung","#model-state .txt{display:none}"):
   self.assertIn(needle,html)
 def test_09h_prompt_is_cache_friendly(self):
  # Stable system prompt; volatile memory/files only in the [Kontext] tail; transcript grows at its end.
  def turn(text):
   n=len(self.request('/api/state')['room'])
   self.request('/api/message',{'text':text,'bots':['test']});self.wait_room('Hallo aus dem Modell.');self.wait_idle()
   return n,Model.calls[-1]['messages']
  n1,m1=turn('Cache eins')
  self.request('/api/agent/test/remember',{'fact':'Neue Erinnerung zwischen zwei Runden'})
  n2,m2=turn('Cache zwei')
  self.assertEqual(m1[0]['content'],m2[0]['content'],'System prompt must not change between turns')
  self.assertNotIn('Neue Erinnerung',m2[0]['content'])
  self.assertIn('[Kontext]',m2[1]['content']);self.assertIn('Neue Erinnerung',m2[1]['content'].split('[Kontext]')[1])
  start=lambda n:((n+1-30)//10)*10 if n+1>30 else 0   # +1: the new user message
  if start(n1)==start(n2):
   self.assertTrue(m2[1]['content'].startswith(m1[1]['content'].split('\n[Kontext]')[0]),'Transcript must only grow at its end')
  self.assertTrue(Model.calls[-1]['return_progress']);self.assertTrue(Model.calls[-1]['cache_prompt'])
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
  for needle in ("event.type==='progress'",'Liest Verlauf','streamClock'):self.assertIn(needle,html)
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
    if not s['busy'] and len(Model.calls)>=before+2:break
    time.sleep(.05)
   time.sleep(.3)
   self.assertEqual(len(Model.calls),before+2,'Queued messages for the same bot are merged into one turn')
   answers=[m for m in self.request('/api/state')['room'] if m.get('role')=='assistant' and m.get('content')=='Hallo aus dem Modell.']
   self.assertGreaterEqual(len(answers),2,'The running turn must finish instead of being aborted')
   last=Model.calls[-1]['messages'][1]['content']
   self.assertIn('Zweite Frage',last);self.assertIn('Dritte Frage',last)
   html=self.request('/',raw=True).decode()
   self.assertIn("event.type==='queued'",html);self.assertIn('r&&r.queued',html)
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
   else:self.fail('Verified tool action did not yield one controlled continuation')
   self.assertEqual(len(Model.calls),initial+3)
   self.assertFalse(any('[[MAG:' in x for x in bot_messages),'Do not persist internal markers')
   time.sleep(1.2)
   self.assertEqual(len(Model.calls),initial+3,'Final status must not trigger another bot turn')
   self.assertTrue(state['autotalk'],'Autonomy may remain enabled while idle')
  finally:
   self.request('/api/stop',{});Model.script=[]
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
                   'browser-type','browser-back','browser-scroll','browser-read'):
   self.assertNotIn(obsolete,html)
  self.assertIn("content.innerHTML=safeMarkdown(s.text)",html)
  self.assertIn("createImageBitmap",html)
  self.assertNotIn('notice();screen();',html)
  self.error(404,'/api/console/test',{'command':'whoami'}) # no HTTP terminal fallback
 def test_11_named_remote_desktop_routes_follow_agent_lifecycle(self):
  # Every persisted agent id becomes a stable Tailnet HTTPS path. No proxy
  # config or new browser profile is generated by this request.
  html=self.request('/bot/test/',raw=True).decode()
  self.assertIn('const remoteBotId=',html)
  self.assertIn("remote.href='/bot/'+encodeURIComponent(a.id)+'/'",html)
  self.assertIn("$('browser-agent').value=own.id",html)
  self.assertIn('id="browser-fullscreen"',html)
  self.assertIn('id="terminal-fullscreen"',html)
  self.assertIn('id="remote-bar"',html)
  self.assertIn('body.remote-mode',html)
  self.assertNotIn('window.open(',html)
  self.assertEqual(self.request('/bot/test',raw=True),html.encode())
  for invalid in ('/bot/not-registered/','/bot/test/extra','/bot/test%2fextra','/bot/../../'):
   self.error(404,invalid)
  # A direct guest bridge must never expose the human remote desktop route.
  token=self.state.joinpath('token').read_text().strip()
  self.error(403,'/bot/test/',bridge=True,headers={'Authorization':'Bearer '+token})
 def test_11_browser_live_requires_known_bot(self):
  self.error(404,'/api/browser-live/offer?bot=not-a-bot',{'type':'offer','sdp':'sdp'})
  self.error(404,'/api/browser/not-a-bot/pointer',{'x':1,'y':1})
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

class SingleBot(unittest.TestCase):
 """MAG_ROOM_SINGLE_BOT: exactly one bot; other stored bots are parked, never lost."""
 @classmethod
 def setUpClass(cls):
  cls.tmp=tempfile.TemporaryDirectory();cls.state=pathlib.Path(cls.tmp.name)/'state';cls.state.mkdir()
  cls.state.joinpath('agents.json').write_text(json.dumps([{'id':'alt','name':'Alter Bot','vm':'','memory':[{'text':'bleibt erhalten'}]}]))
  cls.model=http.server.ThreadingHTTPServer(('127.0.0.1',0),Model);threading.Thread(target=cls.model.serve_forever,daemon=True).start();cls.ui=port();cls.bridge=port()
  env=dict(os.environ,MAG_ROOM_STATE=str(cls.state),MAG_ROOM_PORT=str(cls.ui),MAG_ROOM_BRIDGE_PORT=str(cls.bridge),MAG_ROOM_BRIDGE_IP='127.0.0.1',MAG_ROOM_BACKEND=f'http://127.0.0.1:{cls.model.server_port}/v1/chat/completions',MAG_ROOM_UI=str(ROOT/'mag-room/web/index.html'),MAG_ROOM_STATIC=str(ROOT/'mag-room/static'),MAG_ROOM_MODEL_STATUS=str(pathlib.Path(cls.tmp.name)/'model-status.json'),MAG_ROOM_SINGLE_BOT='deepseek-chat')
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
  for needle in ("html-preview","sandbox','allow-scripts'","$('message').focus()","autofocus","e.key==='Enter'&&!e.shiftKey"):self.assertIn(needle,html)
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
  env=dict(os.environ,MAG_ROOM_STATE=str(cls.state),MAG_ROOM_PORT=str(cls.ui),MAG_ROOM_BRIDGE_PORT=str(cls.bridge),MAG_ROOM_BRIDGE_IP='127.0.0.1',MAG_ROOM_BACKEND=f'http://127.0.0.1:{cls.model.server_port}/v1/chat/completions',MAG_ROOM_UI=str(ROOT/'mag-room/web/index.html'),MAG_ROOM_STATIC=str(ROOT/'mag-room/static'),MAG_ROOM_MODEL_STATUS=str(pathlib.Path(cls.tmp.name)/'model-status.json'),MAG_ROOM_SINGLE_BOT='deepseek-chat',MAG_ROOM_SINGLE_BOT_VM='on',MAG_ROOM_AGENT_CTL=str(cls.ctl))
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
  self.assertIn('spawn agent2',(self.ctl.parent/'ctl.log').read_text())
  stored={a['id']:a for a in json.loads(self.state.joinpath('agents.json').read_text())}
  self.assertEqual(stored['deepseek-chat']['vm'],'agent2');self.assertEqual(stored['alt']['vm'],'agent1')
if __name__=='__main__':unittest.main(verbosity=2)
