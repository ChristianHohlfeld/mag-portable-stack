"""Playwright: a bot's routine proposal shows a confirmation card (Speichern /
Ablehnen); a secret request shows a masked card whose value never appears in
the page or the room. Phone portrait."""
# mag-test-timeout: 180
import pathlib as _mag_path
import sys as _mag_sys
_mag_root = _mag_path.Path(__file__).resolve()
while _mag_root != _mag_root.parent and not (_mag_root / 'ops/ci/mag_deadline.py').is_file():
    _mag_root = _mag_root.parent
_mag_sys.path.insert(0, str(_mag_root / 'ops/ci'))
import mag_deadline as _mag_deadline
_mag_deadline.arm(180)
import importlib.util,json,os,pathlib,shutil,tempfile,unittest
ROOT=pathlib.Path(__file__).resolve().parents[2]
# CI uses a writable, reproducible screenshots directory. The former
# /opt/cursor/artifacts only exists in an optional developer environment.
ART=pathlib.Path(os.environ.get('MAG_E2E_SHOTS',str(pathlib.Path(tempfile.gettempdir())/'mag-e2e-shots')))
spec=importlib.util.spec_from_file_location('test_pwa',ROOT/'mag-room/tests/test_pwa.py');pwa=importlib.util.module_from_spec(spec);spec.loader.exec_module(pwa)
def call(name,args):return [{'tool_calls':[{'index':0,'id':'c_'+name,'function':{'name':name,'arguments':json.dumps(args)}}]}]
class Cards(unittest.TestCase):
 def test_routine_and_secret_cards(self):
  try:from playwright.sync_api import sync_playwright
  except ImportError:self.skipTest('playwright is not installed')
  ART.mkdir(parents=True,exist_ok=True)
  tmp=pathlib.Path('/tmp/mag-cards-ui')
  if tmp.exists():shutil.rmtree(tmp)
  static=tmp/'static';shutil.copytree(ROOT/'mag-room/static',static)
  proc,log,model,ui,bridge=pwa.start_room(tmp,static,'cards',extra={'MAG_ROOM_PREWARM':'0','MAG_ROOM_MCP_AUTO':'0'})
  errors=[];pw=sync_playwright().start()
  browser=pwa.launch_chromium(pw,headless=True,args=['--no-sandbox','--disable-dev-shm-usage'])
  VALUE='Pa55-wort-geheim-91'
  try:
   ctx=browser.new_context(viewport={'width':390,'height':844},has_touch=True,is_mobile=True);page=ctx.new_page()
   page.on('pageerror',lambda e:errors.append(str(e)))
   page.goto('http://127.0.0.1:%s/'%ui,wait_until='domcontentloaded')
   pwa.Health.script=[call('jobs_create',{'name':'Morgenbericht','prompt':'Fasse zusammen','schedule':{'kind':'daily','time':'07:00'},'idempotency_key':'ui-morgen-1'}),[{'content':'Routine gespeichert.'}]]
   page.locator('#message').fill('Jeden Morgen einen Bericht');page.locator('#send-button').click()
   card=page.locator('.routine-card')
   try:card.wait_for(timeout=30000)
   except Exception:page.screenshot(path=str(ART/'routine_card_fail.png'));print('ERRS',errors,page.evaluate("()=>[typeof window.magAgentUI,mcpApprovals.size,document.querySelectorAll('.mcp-approve').length,typeof updateStream]"),pwa.api(ui,'/api/state').get('approvals'));raise
   self.assertIn('Morgenbericht',card.inner_text());self.assertIn('täglich 07:00',card.inner_text());self.assertIn('Fasse zusammen',card.inner_text())
   page.screenshot(path=str(ART/'routine_card.png'))
   card.locator('button',has_text='Speichern').click()
   page.wait_for_function("()=>[...document.querySelectorAll('.job-name')].some(n=>n.textContent==='Morgenbericht')",timeout=15000)
   page.wait_for_function("()=>[...document.querySelectorAll('.message.assistant .content')].some(n=>n.textContent.trim()==='Routine gespeichert.')",timeout=15000)
   pwa.Health.script=[call('secret',{'action':'request','name':'SHOP_PW','purpose':'Login im Shop'}),[{'content':'Danke.'}]]
   page.locator('#message').fill('Logg dich im Shop ein');page.locator('#send-button').click()
   sc=page.locator('.secret-card');sc.wait_for(timeout=15000)
   inp=sc.locator('input[type=password]');self.assertEqual(inp.get_attribute('autocomplete'),'new-password')
   inp.fill(VALUE);page.screenshot(path=str(ART/'secret_card.png'))
   sc.locator('button',has_text='Sicher speichern').click()
   page.wait_for_function("()=>[...document.querySelectorAll('.message')].some(n=>n.textContent.includes('Danke.'))",timeout=15000)
   self.assertNotIn(VALUE,page.content())
   lst=pwa.api(ui,'/api/secrets')['secrets'];self.assertEqual(lst[0]['name'],'SHOP_PW')
   self.assertNotIn(VALUE,json.dumps(pwa.api(ui,'/api/state')))
   ctx.close();self.assertEqual(errors,[])
  finally:
   pwa.Health.script=[];pwa.stop_room(proc,log,model);browser.close();pw.stop()
if __name__=='__main__':unittest.main(verbosity=2)
