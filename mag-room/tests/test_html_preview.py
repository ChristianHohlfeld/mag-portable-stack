"""Playwright: an ```html answer keeps its code block (with Kopieren) and gets a
live preview below it: a sandboxed srcdoc iframe (allow-scripts, never
allow-same-origin) whose module script really runs and draws. Toggle hides it,
Vollbild covers the viewport. Phone portrait, no page errors."""
# mag-test-timeout: 180
import pathlib as _mag_path
import sys as _mag_sys
_mag_root = _mag_path.Path(__file__).resolve()
while _mag_root != _mag_root.parent and not (_mag_root / 'ops/ci/mag_deadline.py').is_file():
    _mag_root = _mag_root.parent
_mag_sys.path.insert(0, str(_mag_root / 'ops/ci'))
import mag_deadline as _mag_deadline
_mag_deadline.arm(180)
import importlib.util,os,pathlib,shutil,tempfile,unittest
ROOT=pathlib.Path(__file__).resolve().parents[2]
ART=pathlib.Path(os.environ.get('MAG_E2E_SHOTS',str(pathlib.Path(tempfile.gettempdir())/'mag-e2e-shots')))
spec=importlib.util.spec_from_file_location('test_pwa',ROOT/'mag-room/tests/test_pwa.py');pwa=importlib.util.module_from_spec(spec);spec.loader.exec_module(pwa)
DEMO='''<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>body{margin:0;background:#123}</style></head>
<body><canvas id="c" width="300" height="200"></canvas>
<script type="module">
const g=document.getElementById('c').getContext('2d');g.fillStyle='rgb(255,0,0)';g.fillRect(0,0,300,200);
let leak='blocked';try{leak=String(parent.document.title)}catch(e){}
document.title='ran:'+leak;
</script></body></html>'''
ANSWER='Hier die Demo:\n\n```html\n'+DEMO+'\n```\n\nFertig.'
class HtmlPreview(unittest.TestCase):
 def test_html_block_code_and_sandboxed_preview(self):
  try:from playwright.sync_api import sync_playwright
  except ImportError:self.skipTest('playwright is not installed')
  ART.mkdir(parents=True,exist_ok=True)
  tmp=pathlib.Path('/tmp/mag-html-preview')
  if tmp.exists():shutil.rmtree(tmp)
  static=tmp/'static';shutil.copytree(ROOT/'mag-room/static',static)
  proc,log,model,ui,bridge=pwa.start_room(tmp,static,'hp',extra={'MAG_ROOM_PREWARM':'0','MAG_ROOM_MCP_AUTO':'0'})
  errors=[];pw=sync_playwright().start()
  browser=pwa.launch_chromium(pw,headless=True,args=['--no-sandbox','--disable-dev-shm-usage'])
  try:
   ctx=browser.new_context(viewport={'width':390,'height':844},has_touch=True,is_mobile=True);page=ctx.new_page()
   page.on('pageerror',lambda e:errors.append(str(e)))
   page.on('console',lambda m:errors.append(m.text) if m.type=='error' else None)
   page.goto('http://127.0.0.1:%s/'%ui,wait_until='domcontentloaded')
   pwa.Health.script=[[{'content':ANSWER}]]
   page.locator('#message').fill('Bau mir eine HTML-Demo');page.locator('#send-button').click()
   msg=page.locator('.message.assistant',has_text='Fertig.').last;msg.wait_for(timeout=30000)
   pre=msg.locator('pre').first;self.assertIn('Kopieren',pre.locator('.code-bar').inner_text())
   self.assertIn('getContext',pre.locator('code').inner_text())
   box=msg.locator('.code-preview');box.wait_for(state='visible',timeout=10000)
   frame=box.locator('iframe')
   self.assertEqual(frame.get_attribute('sandbox'),'allow-scripts')
   box.scroll_into_view_if_needed()
   page.wait_for_function("()=>{const f=document.querySelector('.code-preview iframe');return f&&f.srcdoc.includes('getContext')}",timeout=10000)
   fr=page.frame_locator('.code-preview iframe')
   fr.locator('canvas').wait_for(timeout=10000)
   child=[f for f in page.frames if f!=page.main_frame][-1]
   child.wait_for_function("()=>document.title.startsWith('ran:')",timeout=10000)
   self.assertEqual(child.evaluate('document.title'),'ran:blocked')  # opaque origin, no parent access
   self.assertEqual(child.evaluate('origin'),'null')
   px=child.evaluate("()=>[...document.getElementById('c').getContext('2d').getImageData(10,10,1,1).data]")
   self.assertEqual(px[:3],[255,0,0])
   page.screenshot(path=str(ART/'html_preview.png'))
   toggle=msg.locator('.preview-toggle');toggle.click();self.assertFalse(box.is_visible());toggle.click();box.wait_for(state='visible')
   box.locator('button',has_text='Vollbild').click()
   bb=box.bounding_box();self.assertGreaterEqual(bb['height'],800)
   box.locator('button',has_text='Schließen').click()
   self.assertEqual(errors,[])
   ctx.close()
  finally:
   pwa.Health.script=[];pwa.stop_room(proc,log,model);browser.close();pw.stop()
if __name__=='__main__':unittest.main(verbosity=2)
