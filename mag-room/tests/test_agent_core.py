"""Grok-style turn model: todo goals keep a turn going (bounded), memory
journal/recall, background sub-agent tasks with real cancel. Fake model."""
# mag-test-timeout: 180
import pathlib as _mag_path
import sys as _mag_sys
_mag_root = _mag_path.Path(__file__).resolve()
while _mag_root != _mag_root.parent and not (_mag_root / 'ops/ci/mag_deadline.py').is_file():
    _mag_root = _mag_root.parent
_mag_sys.path.insert(0, str(_mag_root / 'ops/ci'))
import mag_deadline as _mag_deadline
_mag_deadline.arm(180)
import http.server,json,os,pathlib,subprocess,sys,tempfile,threading,time,unittest
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parent))
import test_native as tn
port,ROOT,NETMODE=tn.port,tn.ROOT,tn.NETMODE

def call(name,args,cid='c1'):
 return [{'tool_calls':[{'index':0,'id':cid,'function':{'name':name,'arguments':json.dumps(args)}}]}]

class Router(http.server.BaseHTTPRequestHandler):
 """Main-turn and sub-agent requests get separate scripts (they run concurrently)."""
 protocol_version='HTTP/1.1';main=[];task=[];calls=[]
 def log_message(self,*a):pass
 def do_GET(self):
  data=b'{"status":"ok"}' if self.path=='/health' else b'{"data":[{"id":"deepseek-v4-flash-0731"}]}'
  self.send_response(200);self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
 def do_POST(self):
  obj=json.loads(self.rfile.read(int(self.headers['Content-Length'])));type(self).calls.append(obj)
  is_task=any('Hintergrundaufgabe' in str(m.get('content')) for m in obj.get('messages',[]))
  q=type(self).task if is_task else type(self).main
  chunks=q.pop(0) if q else ['Ok.']
  self.send_response(200);self.send_header('Transfer-Encoding','chunked');self.end_headers()
  try:
   for piece in chunks:
    if isinstance(piece,dict) and '_sleep' in piece:time.sleep(piece['_sleep']);continue
    delta=piece if isinstance(piece,dict) else {'content':piece}
    ev=b'data: '+json.dumps({'choices':[{'delta':delta}]}).encode()+b'\n\n'
    self.wfile.write(('%x\r\n'%len(ev)).encode()+ev+b'\r\n');self.wfile.flush()
   end=b'data: [DONE]\n\n';self.wfile.write(('%x\r\n'%len(end)).encode()+end+b'\r\n0\r\n\r\n');self.wfile.flush()
  except (BrokenPipeError,ConnectionResetError):pass

class AgentCore(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.tmp=tempfile.TemporaryDirectory();cls.state=pathlib.Path(cls.tmp.name)/'state';cls.state.mkdir()
  cls.state.joinpath('agents.json').write_text(json.dumps([{'id':'ali','name':'Ali','vm':'','memory':[{'text':'Chris mag kurze Antworten'}]}]))
  cls.model=http.server.ThreadingHTTPServer(('127.0.0.1',0),Router);threading.Thread(target=cls.model.serve_forever,daemon=True).start()
  cls.ui=port();cls.bridge=port()
  env=dict(os.environ,MAG_OWNER_CANONICAL_LOGIN='chris@github',MAG_TEST_ALLOW_LOCAL_OWNER='1',MAG_ROOM_STATE=str(cls.state),
   MAG_ROOM_PORT=str(cls.ui),MAG_ROOM_BRIDGE_PORT=str(cls.bridge),MAG_ROOM_BRIDGE_IP='127.0.0.1',
   MAG_ROOM_BACKEND=f'http://127.0.0.1:{cls.model.server_port}/v1/chat/completions',MAG_ROOM_UI=str(ROOT/'mag-room/web/index.html'),
   MAG_ROOM_STATIC=str(ROOT/'mag-room/static'),MAG_ROOM_MODEL_STATUS=str(pathlib.Path(cls.tmp.name)/'ms.json'),MAG_NETMODE=str(NETMODE),
   MAG_TEST_NO_MEMGIT='1',MAG_ROOM_MCP_AUTO='0',MAG_ROOM_MAX_TASKS='1',MAG_ROOM_PREWARM='0')
  env.pop('MAG_ROOM_SINGLE_BOT',None)
  cls.env=env.copy()
  last=None
  for attempt in range(6):
   if attempt:
    cls.ui=port();cls.bridge=port()
    env['MAG_ROOM_PORT']=str(cls.ui);env['MAG_ROOM_BRIDGE_PORT']=str(cls.bridge)
   cls.log=open(pathlib.Path(cls.tmp.name)/'server.log','a');cls.proc=subprocess.Popen([str(ROOT/'build/magroom')],env=env,stdout=cls.log,stderr=cls.log)
   try:tn.wait_http(f'http://127.0.0.1:{cls.ui}/healthz',timeout=8,proc=cls.proc);return
   except Exception as e:
    last=e
    if cls.proc.poll() is None:
     cls.proc.terminate()
     try:cls.proc.wait(timeout=3)
     except Exception:cls.proc.kill();cls.proc.wait(timeout=3)
    cls.log.close()
  raise RuntimeError('startup failed: %s'%last)
 @classmethod
 def tearDownClass(cls):
  cls.proc.terminate();cls.proc.wait(timeout=5);cls.log.close();cls.model.shutdown();cls.tmp.cleanup()
 request=classmethod(tn.Native.request.__func__)
 wait_idle=tn.Native.wait_idle
 def setUp(self):
  self.wait_idle()
  for _ in range(60):
   if not any(t['status']=='running' for t in self.request('/api/tasks')['tasks']):break
   time.sleep(.05)
  Router.main=[];Router.task=[];Router.calls=[]
 def ask(self,text):
  self.request('/api/message',{'text':text,'bots':['ali']})
  time.sleep(.2);self.wait_idle()
 def until(self,pred,secs=5):
  end=time.time()+secs
  while time.time()<end:
   v=pred()
   if v:return v
   time.sleep(.05)
  self.fail('condition not reached')
 def room(self):return self.request('/api/state')['room']
 def test_01_idle_answer_is_one_call(self):
  Router.main=[['Hallo.']];self.ask('Hi')
  self.assertEqual(len(Router.calls),1)
 def test_02_open_todos_continue_until_done(self):
  Router.main=[call('todo',{'action':'set','goal':'Bericht','items':['Daten holen','Zusammenfassen']}),
   ['Ich bin dann mal fertig.'],
   call('todo',{'action':'done','item':1},'c2'),
   call('todo',{'action':'block','item':2,'text':'Quelle offline'},'c3'),
   ['Erledigt: Daten geholt; Zusammenfassung blockiert (Quelle offline).']]
  self.ask('Mach einen Bericht')
  self.assertEqual(len(Router.calls),5)
  self.assertIn('Deine Ziele sind noch offen',json.dumps(Router.calls[2]['messages'],ensure_ascii=False))
  todo=self.request('/api/todo/ali')
  self.assertEqual([i['status'] for i in todo['items']],['done','blocked'])
  self.assertIn('Erledigt: Daten geholt',self.room()[-1]['content'])
 def test_02b_new_request_does_not_inherit_previous_open_todos(self):
  # Regression: bot-scoped TODOs from an earlier user goal used to keep
  # unrelated requests alive. A goal-scoped plan is loaded independently.
  Router.main=[call('todo',{'action':'set','goal':'Erster Auftrag','items':['unfertig']}),
               ['Ich versuche es.'],['Ich versuche es.'],['Ich versuche es.']]
  self.ask('Erster Auftrag')
  first=self.request('/api/todo/ali')
  self.assertEqual(first['goal'],'Erster Auftrag')
  self.assertEqual(first['items'][0]['status'],'open')
  Router.calls=[]
  Router.main=[call('todo',{'action':'list'}),['Zweiter Auftrag erledigt.']]
  self.ask('Zweite unabhaengige Frage')
  self.assertEqual(len(Router.calls),2,
                   'Keine Fortsetzung des alten Plans nach neuem User-Turn')
  results=[m['content'] for m in Router.calls[-1]['messages']
           if m.get('role')=='tool']
  self.assertTrue(any('keine Ziele' in x for x in results), results)
  # Latest-view compatibility still exposes the previous plan until a new
  # todo(set) commits one; its per-goal source stays isolated.
  files=list((self.state/'todo').glob('ali_g*.json'))
  self.assertGreaterEqual(len(files),1)
  self.assertTrue(any(json.loads(p.read_text()).get('goal')=='Erster Auftrag'
                      for p in files))

 def test_03_todo_nudge_is_bounded(self):
  Router.main=[call('todo',{'action':'set','items':['A']}),['Fertig.'],['Fertig.'],['Fertig.'],['Nie erreicht.']]
  self.ask('Endlos?')
  self.assertEqual(len(Router.calls),4)
 def test_04_todo_in_system_rules_and_tools(self):
  Router.main=[['x']];self.ask('Regeln?')
  names={t['function']['name'] for t in Router.calls[0]['tools']}
  self.assertTrue({'todo','memory','task','remember'}<=names)
  self.assertIn('todo(set)',Router.calls[0]['messages'][0]['content'])
 def test_05_memory_journal_recall_update_forget(self):
  Router.main=[call('memory',{'action':'journal','text':'Server MAG neu gestartet'}),
   call('memory',{'action':'recall','query':'kurze'},'c2'),
   call('memory',{'action':'update','item':1,'text':'Chris mag sehr kurze Antworten'},'c3'),
   call('remember',{'fact':'Wegwerf'},'c4'),call('memory',{'action':'forget','item':2},'c5'),['Ok.']]
  self.ask('Merk dir was')
  tool_msgs=[m['content'] for m in Router.calls[2]['messages'] if m.get('role')=='tool']
  self.assertTrue(any('Profil #1: Chris mag kurze Antworten' in c for c in tool_msgs),tool_msgs)
  j=self.request('/api/journal/ali')['journal'];self.assertEqual(j[-1]['text'],'Server MAG neu gestartet')
  mem=[m['text'] for m in json.loads(self.state.joinpath('agents.json').read_text())[0]['memory']]
  self.assertEqual(mem,['Chris mag sehr kurze Antworten'])
 def test_06_background_task_runs_and_posts_result(self):
  Router.main=[call('task',{'action':'start','title':'Recherche','prompt':'Finde X'}),['Läuft im Hintergrund.']]
  Router.task=[[{'_sleep':.3},'Ergebnis X gefunden.']]
  self.request('/api/message',{'text':'Recherchiere X','bots':['ali']})
  # The task result can land before wait_idle returns, so do not require
  # the ack to stay the last room line.
  self.until(lambda:[m for m in self.room() if 'Läuft im Hintergrund' in m.get('content','')])
  t=self.until(lambda:[t for t in self.request('/api/tasks')['tasks'] if t['status']=='done'])
  self.assertEqual(t[0]['title'],'Recherche')
  msg=self.until(lambda:[m for m in self.room() if 'Aufgabe „Recherche“ · fertig' in m.get('content','')])
  self.assertIn('Ergebnis X gefunden.',msg[0]['content'])
  task_call=[c for c in Router.calls if 'Hintergrundaufgabe' in json.dumps(c['messages'],ensure_ascii=False)][0]
  self.assertIn('Finde X',task_call['messages'][-1]['content'])
 def test_07_stop_cancels_running_task(self):
  Router.main=[call('task',{'action':'start','title':'Lang','prompt':'Dauert'}),['Gestartet.']]
  Router.task=[[{'_sleep':8},'zu spaet']]
  self.ask('Starte lang')
  self.until(lambda:[t for t in self.request('/api/tasks')['tasks'] if t['status']=='running' and t['title']=='Lang'])
  t0=time.time();self.request('/api/cancel',{})
  self.until(lambda:[t for t in self.request('/api/tasks')['tasks'] if t['title']=='Lang' and t['status']=='cancelled'],3)
  self.assertLess(time.time()-t0,3)
  self.until(lambda:[m for m in self.room() if 'Aufgabe „Lang“ · abgebrochen' in m.get('content','')])
 def test_08_cancel_api_and_task_limit(self):
  Router.main=[call('task',{'action':'start','title':'Eins','prompt':'a'}),
   call('task',{'action':'start','title':'Zwei','prompt':'b'},'c2'),['Ok.']]
  Router.task=[[{'_sleep':8},'spaet']]
  self.ask('Zwei Aufgaben')
  main=[c for c in Router.calls if 'Hintergrundaufgabe' not in json.dumps(c['messages'],ensure_ascii=False)]
  tool_msgs=[m['content'] for m in main[2]['messages'] if m.get('role')=='tool']
  self.assertTrue(any('Limit' in c for c in tool_msgs),tool_msgs)
  run=self.until(lambda:[t for t in self.request('/api/tasks')['tasks'] if t['status']=='running'])
  self.assertTrue(self.request(f"/api/tasks/{run[0]['id']}/cancel",{})['cancelled'])
  self.until(lambda:[t for t in self.request('/api/tasks')['tasks'] if t['id']==run[0]['id'] and t['status']=='cancelled'],3)
 def test_09_user_message_waits_for_no_task(self):
  # A running sub-agent never blocks the room: the next user message is answered at once.
  Router.main=[call('task',{'action':'start','title':'Hintergrund','prompt':'warte'}),['Ok.'],['Sofort da.']]
  Router.task=[[{'_sleep':6},'spaet']]
  self.ask('Start')
  t0=time.time();self.ask('Bist du da?')
  self.assertLess(time.time()-t0,3)
  self.assertIn('Sofort da.',self.room()[-1]['content'])
  self.request('/api/cancel',{})

 def test_09z_background_task_survives_restart_without_unsafe_replay(self):
  # Durability invariant: persisted before execution; no automatic replay of
  # potentially irreversible commands after SIGKILL; owner explicitly resumes.
  Router.main=[call('task',{'action':'start','title':'Restart probe','prompt':'Check X and save result'}),['Läuft.']]
  Router.task=[[{'_sleep':8},'stale answer']]
  self.ask('Starte die Restart probe')
  run=self.until(lambda:[t for t in self.request('/api/tasks')['tasks']
                         if t['status']=='running' and t['title']=='Restart probe'])
  task_id=run[0]['id']
  persisted=json.loads(self.state.joinpath('tasks.json').read_text())
  self.assertTrue(any(t['id']==task_id and t['status']=='running' and
                      t['prompt']=='Check X and save result' for t in persisted))
  cls=type(self)
  cls.proc.kill();cls.proc.wait(timeout=4);cls.log.close()
  cls.log=open(pathlib.Path(cls.tmp.name)/'server.log','a')
  cls.proc=subprocess.Popen([str(ROOT/'build/magroom')],env=cls.env,stdout=cls.log,stderr=cls.log)
  tn.wait_http(f'http://127.0.0.1:{cls.ui}/healthz',timeout=8,proc=cls.proc)
  recovered=[t for t in self.request('/api/tasks')['tasks'] if t['id']==task_id][0]
  self.assertEqual(recovered['status'],'interrupted')
  self.assertEqual(recovered['attempts'],1)
  time.sleep(.15)
  self.assertEqual([t['status'] for t in self.request('/api/tasks')['tasks'] if t['id']==task_id],['interrupted'])
  Router.task=[['Geprüft: keine vorherige Aktion nötig.']]
  self.assertTrue(self.request(f'/api/tasks/{task_id}/resume',{})['resumed'])
  finished=self.until(lambda:[t for t in self.request('/api/tasks')['tasks']
                              if t['id']==task_id and t['status']=='done'])
  self.assertEqual(finished[0]['attempts'],2)
  restored=json.loads(self.state.joinpath('tasks.json').read_text())
  self.assertTrue(any(t['id']==task_id and t['status']=='done' for t in restored))
  self.assertTrue(any('WIEDERAUFNAHME' in json.dumps(c['messages'],ensure_ascii=False)
                      for c in Router.calls))
  self.assertFalse(self.request(f'/api/tasks/{task_id}/resume',{})['resumed'])

 def test_09zz_open_background_goals_cannot_be_reported_done(self):
  Router.main=[call('task',{'action':'start','title':'Offene Analyse','prompt':'Recherchiere und verifiziere X'}),['Im Hintergrund.']]
  Router.task=[call('todo',{'action':'set','goal':'Beweis','items':['Quelle prüfen']}),['Fertig.'],['Fertig.'],['Fertig.']]
  self.ask('Bitte X recherchieren')
  outcome=self.until(lambda:[t for t in self.request('/api/tasks')['tasks']
                             if t['title']=='Offene Analyse' and t['status']=='incomplete'])
  self.assertEqual(outcome[0]['todo']['items'][0]['status'],'open')
  self.assertIn('Nicht abgeschlossen',outcome[0]['result'])
  self.assertTrue(any('Aufgabe „Offene Analyse“ · unvollständig' in m.get('content','')
                      for m in self.room()))
  persisted=json.loads(self.state.joinpath('tasks.json').read_text())
  self.assertTrue(any(t['id']==outcome[0]['id'] and t['status']=='incomplete' for t in persisted))

 def approval(self,kind):
  return self.until(lambda:[a for a in self.request('/api/state').get('approvals') or [] if a.get('kind')==kind])[0]
 def tool_results(self):
  return [m['content'] for c in Router.calls for m in c['messages'] if m.get('role')=='tool']
 def test_10_routine_needs_confirmation_and_rejection_is_final(self):
  args={'name':'Morgenbericht','prompt':'Fasse die Nachrichten zusammen','schedule':{'kind':'daily','time':'07:00'},'idempotency_key':'morgen-bericht-1'}
  Router.main=[call('jobs_create',args),['Vorgeschlagen.']]
  self.request('/api/message',{'text':'Jeden Morgen Bericht','bots':['ali']})
  a=self.approval('routine');info=json.loads(a['args'])
  self.assertEqual((info['name'],info['prompt'],info['schedule']['time']),('Morgenbericht','Fasse die Nachrichten zusammen','07:00'))
  self.assertFalse([j for j in self.request('/api/jobs')['jobs'] if j['name']=='Morgenbericht'])
  self.request('/api/plugins/approvals/'+a['id'],{'decision':'deny'});time.sleep(.2);self.wait_idle()
  self.assertTrue(any('Abgelehnt' in r for r in self.tool_results()))
  self.assertFalse([j for j in self.request('/api/jobs')['jobs'] if j['name']=='Morgenbericht'])
  # Same proposal again: refused at once, no new card.
  Router.main=[call('jobs_create',args,'c9'),['Ok.']];Router.calls=[]
  self.ask('Nochmal?')
  self.assertFalse(self.request('/api/state').get('approvals'))
  self.assertTrue(any('bereits abgelehnt' in r for r in self.tool_results()))
 def test_11_routine_saved_after_confirmation(self):
  args={'name':'Abendcheck','prompt':'Pruefe Server','schedule':{'kind':'daily','time':'20:00'},'idempotency_key':'abend-check-1'}
  Router.main=[call('jobs_create',args),['Gespeichert.']]
  self.request('/api/message',{'text':'Abends pruefen','bots':['ali']})
  a=self.approval('routine');self.request('/api/plugins/approvals/'+a['id'],{'decision':'allow'})
  time.sleep(.2);self.wait_idle()
  j=[j for j in self.request('/api/jobs')['jobs'] if j['name']=='Abendcheck'];self.assertEqual(len(j),1,self.tool_results())
  self.assertEqual((j[0]['created_by'],j[0]['bot']),('bot','ali'))
  self.request('/api/jobs/'+j[0]['id'],method='DELETE')
 def test_12_secret_card_value_never_reaches_model_room_or_logs(self):
  VALUE='Hunter2-geheim-7f3a9c'
  Router.main=[call('secret',{'action':'request','name':'shop_password','purpose':'Login im Shop'}),
   call('remember',{'fact':'pw {{secret:SHOP_PASSWORD}}'},'c2'),
   call('secret',{'action':'list'},'c3'),['Fertig.']]
  self.request('/api/message',{'text':'Logge dich im Shop ein','bots':['ali']})
  a=self.approval('secret');self.assertEqual(json.loads(a['args'])['name'],'SHOP_PASSWORD')
  self.assertNotIn('value',a['args'])
  self.request('/api/secrets/requests/'+a['id'],{'value':VALUE});time.sleep(.2);self.wait_idle()
  res=self.tool_results()
  self.assertTrue(any('{{secret:SHOP_PASSWORD}}' in r for r in res),res)
  self.assertTrue(any('nur in browser_type' in r for r in res),res)  # handle refused outside allowed tools
  lst=self.request('/api/secrets')['secrets'];self.assertEqual(lst[0]['name'],'SHOP_PASSWORD');self.assertEqual(lst[0]['bots'],['ali'])
  # Nowhere in clear text: model requests, state, room, server log, state files.
  self.assertNotIn(VALUE,json.dumps(Router.calls));self.assertNotIn(VALUE,json.dumps(self.request('/api/state')))
  self.assertNotIn(VALUE,json.dumps(self.request('/api/secrets')))
  self.log.flush();self.assertNotIn(VALUE,(pathlib.Path(self.tmp.name)/'server.log').read_text())
  for f in self.state.rglob('*'):
   if f.is_file():self.assertNotIn(VALUE.encode(),f.read_bytes(),f)
  # Revoke access, then delete.
  self.request('/api/secrets/SHOP_PASSWORD/bots',{'bots':[]},method='PUT')
  self.assertEqual(self.request('/api/secrets')['secrets'][0]['bots'],[])
  self.request('/api/secrets/SHOP_PASSWORD',method='DELETE');self.assertEqual(self.request('/api/secrets')['secrets'],[])
 def test_13_secret_scrubbed_from_tool_results(self):
  VALUE='TokenXYZ-123456789'
  self.request('/api/secrets/API_TOKEN',{'value':VALUE,'purpose':'API'},method='PUT')
  self.request('/api/secrets/API_TOKEN/bots',{'bots':['ali']},method='PUT')
  # memory recall would echo the fact; a stored clear value is scrubbed on the way back.
  Router.main=[call('memory',{'action':'journal','text':'note '+VALUE}),call('memory',{'action':'recall','query':'note'},'c2'),['Ok.']]
  self.ask('Notiz')
  res=self.tool_results();self.assertTrue(any('[secret:API_TOKEN]' in r for r in res),res)
  self.assertFalse(any(VALUE in r for r in res))
  self.request('/api/secrets/API_TOKEN',method='DELETE')

FAKE_SWAP=r"""#!/usr/bin/env python3
import json,sys,time,os
_,cmd,node,model=sys.argv;p=os.environ['MAG_NUMA_STATE']
def load():
 try:return json.load(open(p))
 except Exception:return {'nodes':{}}
def save(s):json.dump(s,open(p+'.tmp','w'));os.replace(p+'.tmp',p)
s=load();prev=s['nodes'].get(node,{}).get('model',{'0':'deepseek-v4-flash-0731','1':'deepseek-v4-flash-0731-iq3'}[node])
for pct in (10,50,90):
 s['nodes'][node]={'model':prev,'state':'loading','progress':pct,'target':model};save(s);time.sleep(.4)
if model=='broken':
 s['nodes'][node]={'model':prev,'state':'serving','progress':100,'error':'swap to broken failed (rolled back)'};save(s)
 print('failed: health check timed out; rolled back');sys.exit(6)
s['nodes'][node]={'model':model,'state':'serving','progress':100};save(s)
"""

class NumaRoom(unittest.TestCase):
 """Bots assigned to NUMA nodes; confirmed hot-swap queues that node's bots only."""
 @classmethod
 def setUpClass(cls):
  cls.tmp=tempfile.TemporaryDirectory();t=pathlib.Path(cls.tmp.name);cls.state=t/'state';cls.state.mkdir()
  cls.state.joinpath('agents.json').write_text(json.dumps([{'id':'a0','name':'Ali','vm':'','memory':[]},{'id':'b1','name':'Bea','vm':'','memory':[]}]))
  cat=json.loads((ROOT/'ops/numa/catalog.json').read_text())
  cat['models'].append({'id':'broken','label':'Broken','short':'X','ram_gib':50,'bin':'/bin/false','model':'/x'})
  cat['models'].append({'id':'huge','label':'Huge','short':'H','ram_gib':300,'bin':'/bin/false','model':'/h'})
  (t/'catalog.json').write_text(json.dumps(cat))
  sw=t/'swap';sw.write_text(FAKE_SWAP);sw.chmod(0o755);cls.nstate=t/'numa.json'
  cls.model=http.server.ThreadingHTTPServer(('127.0.0.1',0),Router);threading.Thread(target=cls.model.serve_forever,daemon=True).start()
  cls.ui=port();cls.bridge=port()
  env=dict(os.environ,MAG_OWNER_CANONICAL_LOGIN='chris@github',MAG_TEST_ALLOW_LOCAL_OWNER='1',MAG_ROOM_STATE=str(cls.state),
   MAG_ROOM_PORT=str(cls.ui),MAG_ROOM_BRIDGE_PORT=str(cls.bridge),MAG_ROOM_BRIDGE_IP='127.0.0.1',
   MAG_ROOM_BACKEND=f'http://127.0.0.1:{cls.model.server_port}/v1/chat/completions',MAG_ROOM_UI=str(ROOT/'mag-room/web/index.html'),
   MAG_ROOM_STATIC=str(ROOT/'mag-room/static'),MAG_ROOM_MODEL_STATUS=str(t/'ms.json'),MAG_NETMODE=str(NETMODE),
   MAG_TEST_NO_MEMGIT='1',MAG_ROOM_MCP_AUTO='0',MAG_ROOM_PREWARM='0',MAG_ROOM_STAGGER='off',
   MAG_NUMA_CATALOG=str(t/'catalog.json'),MAG_NUMA_STATE=str(cls.nstate),MAG_NUMA_SWAP_CMD=str(sw))
  env.pop('MAG_ROOM_SINGLE_BOT',None)
  last=None
  for attempt in range(6):
   if attempt:
    cls.ui=port();cls.bridge=port()
    env['MAG_ROOM_PORT']=str(cls.ui);env['MAG_ROOM_BRIDGE_PORT']=str(cls.bridge)
   cls.log=open(t/'server.log','a');cls.proc=subprocess.Popen([str(ROOT/'build/magroom')],env=env,stdout=cls.log,stderr=cls.log)
   try:tn.wait_http(f'http://127.0.0.1:{cls.ui}/healthz',timeout=8,proc=cls.proc);return
   except Exception as e:
    last=e
    if cls.proc.poll() is None:
     cls.proc.terminate()
     try:cls.proc.wait(timeout=3)
     except Exception:cls.proc.kill();cls.proc.wait(timeout=3)
    cls.log.close()
  raise RuntimeError('startup failed: %s'%last)
 @classmethod
 def tearDownClass(cls):
  cls.proc.terminate();cls.proc.wait(timeout=5);cls.log.close();cls.model.shutdown();cls.tmp.cleanup()
 request=classmethod(tn.Native.request.__func__)
 error=tn.Native.error
 wait_idle=tn.Native.wait_idle
 until=AgentCore.until
 def numa(self):return self.request('/api/numa')
 def swap_done(self):self.until(lambda:self.numa()['swapping']==-1,10)
 def test_01_defaults_two_nodes_and_admission_flags(self):
  n=self.numa();self.assertTrue(n['enabled']);self.assertEqual([x['model'] for x in n['nodes']],['deepseek-v4-flash-0731','deepseek-v4-flash-0731-iq3'])
  fits={c['id']:c['fits'] for c in n['catalog']};self.assertEqual(fits['huge'],[False,False]);self.assertEqual(fits['qwen3.8-flash-next-uncensored'],[True,True])
  ag={a['id']:a for a in self.request('/api/state')['agents']}
  self.assertEqual((ag['a0']['numa'],ag['b1']['numa']),(0,1))
  self.assertEqual(ag['b1']['model'],'deepseek-v4-flash-0731-iq3')
  self.assertEqual(len(self.request('/api/state')['models']),2)
 def test_02_swap_needs_confirm_known_model_and_budget(self):
  self.error(400,'/api/numa/swap',{'node':1,'model':'qwen3.8-flash-next-uncensored'})
  self.error(400,'/api/numa/swap',{'node':1,'model':'nope','confirm':True})
  self.error(400,'/api/numa/swap',{'node':2,'model':'qwen3.8-flash-next-uncensored','confirm':True})
  self.error(409,'/api/numa/swap',{'node':1,'model':'huge','confirm':True})
  self.assertFalse(self.nstate.exists())  # nothing ran
 def test_03_swap_queues_that_node_only_and_switches_traffic(self):
  self.wait_idle();Router.main=[];Router.calls=[]
  r=self.request('/api/numa/swap',{'node':1,'model':'qwen3.8-flash-next-uncensored','confirm':True})
  self.assertTrue(r['accepted']);self.assertEqual(r['swapping'],1)
  self.error(409,'/api/numa/swap',{'node':0,'model':'deepseek-v4-flash-0731-iq3','confirm':True})
  self.request('/api/message',{'text':'Hallo Bea','bots':['b1']})
  self.request('/api/message',{'text':'Hallo Ali','bots':['a0']})
  # NUMA0 answers while NUMA1 loads; Bea's message waits.
  self.until(lambda:any(c['model']=='deepseek-v4-flash-0731' for c in Router.calls),3)
  self.assertFalse(any(c['model']!='deepseek-v4-flash-0731' for c in Router.calls))
  st=self.request('/api/state');b=[a for a in st['agents'] if a['id']=='b1'][0];self.assertTrue(b['swapping'])
  self.assertIn(self.numa()['nodes'][1]['state'],('loading','swapping'))
  self.swap_done()
  self.until(lambda:any(c['model']=='qwen3.8-flash-next-uncensored' for c in Router.calls),5)
  self.assertEqual(self.numa()['nodes'][1]['model'],'qwen3.8-flash-next-uncensored')
  self.assertEqual(self.numa()['nodes'][0]['model'],'deepseek-v4-flash-0731')
 def test_04_failed_swap_keeps_previous_model(self):
  self.swap_done()
  self.request('/api/numa/swap',{'node':1,'model':'broken','confirm':True});self.swap_done()
  n=self.numa();self.assertEqual(n['nodes'][1]['model'],'qwen3.8-flash-next-uncensored')
  self.assertIn('rolled back',n['last_error'])
 def test_05_assignment_is_persisted(self):
  self.request('/api/agents/b1/numa',{'node':0},method='PUT')
  ag={a['id']:a for a in self.request('/api/state')['agents']}
  self.assertEqual((ag['b1']['numa'],ag['b1']['model']),(0,'deepseek-v4-flash-0731'))
  self.assertEqual(json.loads(self.state.joinpath('numa-assign.json').read_text())['b1'],0)
  self.error(400,'/api/agents/b1/numa',{'node':3},method='PUT')
  self.request('/api/agents/b1/numa',{'node':1},method='PUT')

if __name__=='__main__':unittest.main(verbosity=2)
