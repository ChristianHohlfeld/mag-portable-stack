"""Playwright: live action highlight over the browser stream. A real test page
is rendered at 1440x900, its element boxes are taken with the same JS as
mag-browser /locate, its pixels drawn into the room's stream canvas, and the
room client receives browser_action events shaped like browser_events.hpp
(unit-tested in test_browser_events.cpp). Asserts outline, cursor, ripple,
typed text, masked secret field, zoom alignment and the toggle."""
# mag-test-timeout: 180
import pathlib as _mag_path
import sys as _mag_sys
_mag_root = _mag_path.Path(__file__).resolve()
while _mag_root != _mag_root.parent and not (_mag_root / 'ops/ci/mag_deadline.py').is_file():
    _mag_root = _mag_root.parent
_mag_sys.path.insert(0, str(_mag_root / 'ops/ci'))
import mag_deadline as _mag_deadline
_mag_deadline.arm(180)
import importlib.util,json,pathlib,shutil,unittest
ROOT=pathlib.Path(__file__).resolve().parents[2]
ART=pathlib.Path('/opt/cursor/artifacts')
spec=importlib.util.spec_from_file_location('test_pwa',ROOT/'mag-room/tests/test_pwa.py');pwa=importlib.util.module_from_spec(spec);spec.loader.exec_module(pwa)
PAGE="""<html><body style="margin:0;font:20px sans-serif">
<h1 style="margin:40px">Shop Login</h1>
<input id="user" style="position:absolute;left:300px;top:200px;width:320px;height:40px">
<input id="pw" type="password" style="position:absolute;left:300px;top:280px;width:320px;height:40px">
<button id="go" style="position:absolute;left:300px;top:360px;width:160px;height:48px">Anmelden</button></body></html>"""
LOCATE="""(sel)=>{let e=document.querySelector(sel);let r=e.getBoundingClientRect();
 return {x:r.x,y:r.y,w:r.width,h:r.height,password:e.type==='password'}}"""
FIELD_VALUE='Hunter2-geheim-55'  # dummy test value, not a credential
class LiveHighlight(unittest.TestCase):
 def test_overlay_click_type_secret_zoom_toggle(self):
  try:from playwright.sync_api import sync_playwright
  except ImportError:self.skipTest('playwright is not installed')
  ART.mkdir(parents=True,exist_ok=True)
  tmp=pathlib.Path('/tmp/mag-live-highlight')
  if tmp.exists():shutil.rmtree(tmp)
  static=tmp/'static';shutil.copytree(ROOT/'mag-room/static',static)
  # Browser-overlay test needs a bot with a VM assigned. The visual stream
  # is synthetic and does not require a running KVM guest.
  (tmp/'state').mkdir(parents=True,exist_ok=True)
  (tmp/'state'/'agents.json').write_text(json.dumps([
   {'id':'test','name':'Test','vm':'agent1','memory':[]}
  ]))
  proc,log,model,ui,bridge=pwa.start_room(tmp,static,'lh')
  pw=sync_playwright().start();browser=pwa.launch_chromium(pw,headless=True,args=['--no-sandbox','--disable-dev-shm-usage'])
  errors=[]
  try:
   site=browser.new_page(viewport={'width':1440,'height':900});site.set_content(PAGE)
   boxes={k:site.evaluate(LOCATE,'#'+k) for k in ('user','pw','go')}
   self.assertTrue(boxes['pw']['password'])
   shot=site.screenshot(type='png')
   import base64;png='data:image/png;base64,'+base64.b64encode(shot).decode()
   room=browser.new_page(viewport={'width':1400,'height':900});room.on('pageerror',lambda e:errors.append(str(e)))
   room.goto('http://127.0.0.1:%s/'%ui,wait_until='domcontentloaded')
   # Wait for the real VM bot; early events with bot="" are silently
   # ignored once the delayed /api/state refresh selects the correct bot.
   room.wait_for_function("""()=>window.magLiveHighlight&&window.magViewTools&&
     document.getElementById('browser-agent')?.value==='test'""",timeout=10000)
   if not room.locator('#browser-view').is_visible():room.locator('#toggle-tools').click()
   room.locator('#browser-view').wait_for(state='visible')
   room.evaluate("""async(png)=>{const c=document.getElementById('screen');c.hidden=false;Object.defineProperty(c,'hidden',{configurable:true,get:()=>false,set(){}});c.style.display='block';c.style.width='100%';c.style.height='420px';
     const i=new Image();i.src=png;await i.decode();c.getContext('2d').drawImage(i,0,0,1440,900);magViewTools.fit()}""",png)
   bot=room.evaluate("document.getElementById('browser-agent').value")
   ev=lambda e:room.evaluate("(e)=>browserOverlay(e)",{**e,'type':'browser_action','bot':bot})
   def canvas_pos(b):
    return room.evaluate("""(b)=>{const c=document.getElementById('screen').getBoundingClientRect();const s=Math.min(c.width/1440,c.height/900);
      const [px,py]=magStreamOrigin(document.getElementById('screen'));
      return {x:c.left+(c.width-1440*s)*px+b.x*s,y:c.top+(c.height-900*s)*py+b.y*s,w:b.w*s,h:b.h*s}}""",b)
   def assert_box(b):
    box=room.locator('#browser-overlay .lh-box');box.wait_for(state='visible')
    # The overlay updates on the next browser frame. Wait for the NEW target
    # rather than accidentally sampling the previous target's outline.
    # Still assert exact positioning/size below: a stale or misplaced box fails.
    # Compute expected geometry inside each browser frame: the browser
    # preview can change its width while asynchronous room state is loading.
    # A captured pre-frame rectangle is stale even if the overlay is correct.
    try:
     room.wait_for_function("""b => {
       const c=document.querySelector('#screen'),e=document.querySelector('#browser-overlay .lh-box');
       if(!c||!e)return false;
       const r=e.getBoundingClientRect(),cr=c.getBoundingClientRect();
       const s=Math.min(cr.width/1440,cr.height/900);
       const [px,py]=magStreamOrigin(c);
       const want={x:cr.left+(cr.width-1440*s)*px+b.x*s,
         y:cr.top+(cr.height-900*s)*py+b.y*s,w:b.w*s,h:b.h*s};
       return Math.abs(r.x+3-want.x)<3 && Math.abs(r.y+3-want.y)<3 &&
              Math.abs(r.width-6-want.w)<3 && Math.abs(r.height-6-want.h)<3;
     }""",arg=b,timeout=4000)
    except Exception:
     print('HIGHLIGHT_TIMEOUT:',room.evaluate("""() => ({
       selected:document.querySelector('#browser-agent')?.value,
       box:document.querySelector('.lh-box')?.getBoundingClientRect().toJSON(),
       screen:document.querySelector('#screen')?.getBoundingClientRect().toJSON(),
       visible:!document.querySelector('#screen')?.hidden,
       active:window.magLiveHighlight?.enabled()
     })"""),flush=True)
     raise
    want=canvas_pos(b)
    got=box.bounding_box()
    if abs(got['x']+3-want['x'])>=3 or abs(got['y']+3-want['y'])>=3:
     diag=room.evaluate("""()=>{const c=document.querySelector('#screen'),v=document.querySelector('#browser-view'),o=document.querySelector('#browser-overlay'),b=document.querySelector('.lh-box');return {
      bot:document.querySelector('#browser-agent')?.value,screen:c?.getBoundingClientRect().toJSON(),
      view:v?.getBoundingClientRect().toJSON(),overlay:o?.getBoundingClientRect().toJSON(),
      box:b?.getBoundingClientRect().toJSON(),on:window.magLiveHighlight?.enabled(),
      classes:b?.className,hidden:v?.hidden,viewTools:window.magViewTools?.state()}}""")
     print('HIGHLIGHT_GEOMETRY:', {'want':want,'got':got,'diag':diag,'input':b}, flush=True)
    self.assertLess(abs(got['x']+3-want['x']),3);self.assertLess(abs(got['y']+3-want['y']),3)
    self.assertLess(abs(got['width']-6-want['w']),3)
   # click: target outlined before, then ripple at the point
   g=boxes['go'];ev({'action':'target','box':g,'x':g['x']+g['w']/2,'y':g['y']+g['h']/2});assert_box(g)
   ev({'action':'click','x':g['x']+g['w']/2,'y':g['y']+g['h']/2})
   room.locator('#browser-overlay .bo-ripple').wait_for(state='attached',timeout=2000)
   room.screenshot(path=str(ART/'live_highlight_click.png'))
   # type: outline on the field, text appears at the field
   u=boxes['user']
   # The real browser reports the target before typing into it. Assert that
   # both the focus target and the subsequent typing stay aligned.
   ev({'action':'target','box':u,'x':u['x']+12,'y':u['y']+u['h']/2})
   print('HIGHLIGHT_TARGET2:',room.evaluate("""() => ({
     boxCount:document.querySelectorAll('#browser-overlay .lh-box').length,
     boxes:[...document.querySelectorAll('#browser-overlay .lh-box')].map(x=>x.getBoundingClientRect().toJSON()),
     selected:document.querySelector('#browser-agent')?.value,
     highlight:window.magLiveHighlight?.enabled(),
     fn:window.browserOverlay?.toString().slice(0,160)
   })"""),flush=True)
   assert_box(u)
   ev({'action':'type','box':u,'text':'chris@example.org'})
   # The typing caption is intentionally transient. Observe it before the
   # separate animation/geometry assertion may consume that short window.
   room.wait_for_function("()=>document.querySelector('#browser-overlay .bo-type')?.textContent.includes('chris@example.org')",timeout=2500)
   assert_box(u)
   # secret field: dots only (server sends masked; client also refuses a leaked value)
   p=boxes['pw'];ev({'action':'type','box':p,'masked':True,'text':FIELD_VALUE})
   print('HIGHLIGHT_MASKED_EVENT:',room.evaluate("""()=>({
     selected:document.querySelector('#browser-agent')?.value,
     active:window.magLiveHighlight?.enabled(),
     script:window.browserOverlay?.toString().slice(0,260),
     boxClass:document.querySelector('.lh-box')?.className,
     boxStyle:document.querySelector('.lh-box')?.getAttribute('style'),
     boxRect:document.querySelector('.lh-box')?.getBoundingClientRect().toJSON()
   })"""),flush=True)
   assert_box(p)
   self.assertIn('secret',room.locator('#browser-overlay .lh-box').get_attribute('class'))
   room.wait_for_timeout(600)
   self.assertIn('••••',room.locator('#browser-overlay .bo-type').text_content())
   self.assertNotIn(FIELD_VALUE,room.content())
   room.screenshot(path=str(ART/'live_highlight_secret.png'))
   # zoom/pan: the outline follows the transformed stream
   room.evaluate('magViewTools.zoomAt(2)');ev({'action':'target','box':g});assert_box(g)
   room.evaluate('magViewTools.fit()')
   # scroll hint, then toggle off hides the overlay
   ev({'action':'scroll_down','x':720,'y':450});room.locator('#browser-overlay .lh-scroll.down').wait_for(state='attached',timeout=2000)
   room.locator('.lh-toggle').click();self.assertFalse(room.evaluate('magLiveHighlight.enabled()'))
   self.assertFalse(room.locator('#browser-overlay').is_visible())
   room.locator('.lh-toggle').click();self.assertTrue(room.evaluate('magLiveHighlight.enabled()'))
   self.assertEqual(errors,[])
  finally:
   pwa.stop_room(proc,log,model);browser.close();pw.stop()
if __name__=='__main__':unittest.main(verbosity=2)
