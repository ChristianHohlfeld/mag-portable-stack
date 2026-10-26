"""Playwright: browser view zoom (+/-, Ctrl+wheel, pinch), pan, fit, watch vs
take-over (input reaches the bot only in take-over), action caption, console
font zoom; desktop, phone portrait and landscape incl. rotation."""
import importlib.util,pathlib,shutil,unittest
ROOT=pathlib.Path(__file__).resolve().parents[2]
ART=pathlib.Path('/opt/cursor/artifacts')
spec=importlib.util.spec_from_file_location('test_pwa',ROOT/'mag-room/tests/test_pwa.py');pwa=importlib.util.module_from_spec(spec);spec.loader.exec_module(pwa)
SETUP="""()=>{const c=document.getElementById('screen');c.hidden=false;c.style.width='100%';c.style.height='260px';c.style.display='block';
 window.__sent=[];window.liveSend=m=>{__sent.push(m);return true};try{localStorage.removeItem('mag-view-mode')}catch(e){}magViewTools.setMode('watch');magViewTools.fit()}"""
class ViewTools(unittest.TestCase):
 def test_zoom_watch_takeover_rotation(self):
  try:from playwright.sync_api import sync_playwright
  except ImportError:self.skipTest('playwright is not installed')
  tmp=pathlib.Path('/tmp/mag-view-ui')
  if tmp.exists():shutil.rmtree(tmp)
  static=tmp/'static';shutil.copytree(ROOT/'mag-room/static',static)
  proc,log,model,ui,bridge=pwa.start_room(tmp,static,'view')
  pw=sync_playwright().start();browser=pw.chromium.launch(channel='chrome',headless=True,args=['--no-sandbox','--disable-dev-shm-usage'])
  errors=[]
  try:
   for name,vp,mobile in [('desktop',{'width':1280,'height':860},False),('phone',{'width':390,'height':844},True)]:
    ctx=browser.new_context(viewport=vp,has_touch=mobile,is_mobile=mobile);page=ctx.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
    page.goto('http://127.0.0.1:%s/'%ui,wait_until='domcontentloaded');page.wait_for_function('()=>window.magViewTools')
    if not page.locator('#browser-view').is_visible():page.locator('#toggle-tools').click()
    page.locator('#browser-view').wait_for(state='visible')
    page.evaluate(SETUP)
    st=lambda:page.evaluate('magViewTools.state()')
    bar=page.locator('#browser-view .vt-bar')
    bar.locator('button[aria-label=Vergrößern]').click();self.assertAlmostEqual(st()['s'],1.25,2)
    self.assertEqual(bar.locator('.vt-fit').inner_text(),'125%')
    bar.locator('button[aria-label=Verkleinern]').click();self.assertAlmostEqual(st()['s'],1.0,2)
    box=page.locator('#screen').bounding_box();cx,cy=box['x']+box['width']/2,box['y']+box['height']/2
    if not mobile:
     page.mouse.move(cx,cy);page.keyboard.down('Control');page.mouse.wheel(0,-200);page.keyboard.up('Control')
     self.assertGreater(st()['s'],1.05)
     self.assertFalse([m for m in page.evaluate('__sent') if m.get('kind')=='wheel'],'ctrl+wheel zooms, never scrolls the bot')
     # watch mode: drag pans, nothing is sent to the bot
     page.mouse.move(cx,cy);page.mouse.down();page.mouse.move(cx-60,cy-30);page.mouse.up()
     self.assertLess(st()['x'],0);self.assertEqual(page.evaluate('__sent'),[])
     page.keyboard.press('Control+0');self.assertEqual(st()['s'],1)
    else:
     # pinch zoom with two touch pointers
     page.evaluate("""([x,y])=>{const c=document.getElementById('screen');const ev=(t,id,px)=>c.dispatchEvent(new PointerEvent(t,{pointerId:id,clientX:px,clientY:y,bubbles:true,pointerType:'touch'}));
       ev('pointerdown',1,x-20);ev('pointerdown',2,x+20);ev('pointermove',1,x-60);ev('pointermove',2,x+60);ev('pointerup',1,x-60);ev('pointerup',2,x+60)}""",[cx,cy])
     self.assertGreater(st()['s'],2.5);self.assertEqual(page.evaluate('__sent'),[])
     page.screenshot(path=str(ART/'view_zoom_phone.png'))
     # rotate to landscape: zoom stays
     before=st()['s'];page.set_viewport_size({'width':844,'height':390});page.wait_for_timeout(200)
     self.assertAlmostEqual(st()['s'],before,2)
     if not page.locator('#browser-view').is_visible():page.locator('#toggle-tools').click()
     page.evaluate(SETUP)  # no live stream in the fixture: re-show the canvas
    # take over: input now reaches the bot; give back: it stops
    bar.locator('.vt-mode').click();self.assertEqual(st()['mode'],'control')
    page.locator('#screen').click(position={'x':50,'y':50})
    self.assertTrue([m for m in page.evaluate('__sent') if m.get('kind')=='pointer'])
    # zoom != 1: a click maps to the same stream pixel as at 100%
    box=page.locator('#screen').bounding_box();dx,dy=-box['width']*.2,-box['height']*.1
    page.evaluate('__sent.length=0');page.mouse.click(box['x']+box['width']/2+dx,box['y']+box['height']/2+dy)
    ref=[m for m in page.evaluate('__sent') if m.get('kind')=='pointer'][0]
    bar.locator('button[aria-label=Vergrößern]').click();bar.locator('button[aria-label=Vergrößern]').click();s=st()['s'];self.assertGreater(s,1.5)
    page.evaluate('__sent.length=0');ox=box['x']+st()['x']+s*(box['width']/2+dx);oy=box['y']+st()['y']+s*(box['height']/2+dy)
    page.mouse.click(ox,oy);got=[m for m in page.evaluate('__sent') if m.get('kind')=='pointer'][0]
    self.assertAlmostEqual(got['x'],ref['x'],delta=2);self.assertAlmostEqual(got['y'],ref['y'],delta=2)
    bar.locator('.vt-fit').click()
    bar.locator('.vt-mode').click();n=len(page.evaluate('__sent'));page.locator('#screen').click(position={'x':60,'y':60})
    self.assertEqual(len(page.evaluate('__sent')),n)
    # caption follows the bot's browser action
    page.evaluate("()=>{browserOverlay({bot:document.getElementById('browser-agent').value,action:'type',text:'hallo',x:10,y:10})}")
    self.assertIn('tippt',page.locator('.vt-caption').inner_text())
    ctx.close()
   # Phone drawers: title has its own row; compact controls align and stay reachable.
   for vp in ({'width':390,'height':844},{'width':470,'height':1000}):
    ctx=browser.new_context(viewport=vp,has_touch=True,is_mobile=True);page=ctx.new_page()
    page.goto('http://127.0.0.1:%s/'%ui,wait_until='domcontentloaded');page.wait_for_function('()=>window.magViewTools')
    if not page.locator('#browser-view').is_visible():page.locator('#toggle-tools').click()
    page.locator('#browser-view').wait_for(state='visible')
    r=page.evaluate('''()=>{const q=s=>document.querySelector(s),ov=e=>e.scrollWidth-e.clientWidth;const th=q('.terminal-heading');
     const hits=[...document.querySelectorAll('#browser-view .browser-nav button,#browser-view .browser-nav input,.terminal-heading button')].filter(b=>b.offsetParent).map(b=>{const c=b.getBoundingClientRect();const e=document.elementFromPoint(c.x+c.width/2,c.y+c.height/2);return e===b||b.contains(e)});
     const ys=[...th.querySelectorAll('button')].filter(e=>e.offsetParent).map(e=>Math.round(e.getBoundingClientRect().top+e.getBoundingClientRect().height/2));
     return {nav:ov(q('#browser-view .browser-nav')),th:ov(th),rows:Math.max(...ys)-Math.min(...ys),hidden:hits.filter(x=>!x).length,doc:ov(document.documentElement)}}''')
    self.assertEqual((r['nav'],r['th'],r['doc'],r['hidden']),(0,0,0,0),(vp,r));self.assertLessEqual(r['rows'],2,(vp,r))
    # room menu: icons/checkboxes and labels in one aligned column, same icon size
    page.evaluate("()=>{document.getElementById('room-menu').hidden=false}")
    m=page.evaluate('''()=>[...document.querySelectorAll('#room-menu > button, #room-menu > label')].filter(e=>e.offsetParent).map(e=>{const i=e.querySelector('svg,input').getBoundingClientRect(),t=e.querySelector('span').getBoundingClientRect();return [Math.round(i.left),Math.round(i.width),Math.round(t.left)]})''')
    self.assertGreaterEqual(len(m),3);self.assertEqual(len({tuple(x) for x in m}),1,m)
    ctx.close()
   self.assertEqual(errors,[])
  finally:
   pwa.stop_room(proc,log,model);browser.close();pw.stop()
if __name__=='__main__':unittest.main(verbosity=2)
