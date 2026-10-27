"""Playwright: NUMA chips in the top bar, swap sheet, confirm/cancel dialog,
bot->node assignment and compact phone portrait/landscape headers."""
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
FAKE_SWAP=r"""#!/usr/bin/env python3
import json,sys,time,os
_,cmd,node,model=sys.argv;p=os.environ['MAG_NUMA_STATE']
s=json.load(open(p)) if os.path.exists(p) else {'nodes':{}}
for pct in (20,60):
 s['nodes'][node]={'model':'deepseek-v4-flash-0731-iq3','state':'loading','progress':pct};json.dump(s,open(p,'w'));time.sleep(1.2)
s['nodes'][node]={'model':model,'state':'serving','progress':100};json.dump(s,open(p,'w'))
"""
class NumaUI(unittest.TestCase):
 def test_chips_sheet_confirm_cancel_assign(self):
  try:from playwright.sync_api import sync_playwright
  except ImportError:self.skipTest('playwright is not installed')
  ART.mkdir(parents=True,exist_ok=True)
  tmp=pathlib.Path('/tmp/mag-numa-ui')
  if tmp.exists():shutil.rmtree(tmp)
  static=tmp/'static';shutil.copytree(ROOT/'mag-room/static',static)
  (tmp/'state').mkdir(parents=True)
  (tmp/'state'/'agents.json').write_text(json.dumps([{'id':'a0','name':'Ali','vm':'','memory':[]},{'id':'b1','name':'Bea','vm':'','memory':[]}]))
  sw=tmp/'swap';sw.write_text(FAKE_SWAP);sw.chmod(0o755)
  proc,log,model,ui,bridge=pwa.start_room(tmp,static,'numa',extra={'MAG_NUMA_CATALOG':str(ROOT/'ops/numa/catalog.json'),
    'MAG_NUMA_STATE':str(tmp/'numa.json'),'MAG_NUMA_SWAP_CMD':str(sw),'MAG_ROOM_SINGLE_BOT':''})
  errors=[];pw=sync_playwright().start()
  browser=pwa.launch_chromium(pw,headless=True,args=['--no-sandbox','--disable-dev-shm-usage'])
  try:
   for name,vp,mobile in [('phone',{'width':390,'height':844},True),('landscape',{'width':844,'height':390},True),('desktop',{'width':1280,'height':860},False)]:
    ctx=browser.new_context(viewport=vp,has_touch=mobile,is_mobile=mobile);page=ctx.new_page()
    page.on('pageerror',lambda e:errors.append(str(e)))
    page.goto('http://127.0.0.1:%s/'%ui,wait_until='domcontentloaded')
    chips=page.locator('#model-choice .numa-chip');chips.first.wait_for(state='attached')
    self.assertEqual(chips.count(),2)
    self.assertIn('0 · Q4',chips.nth(0).inner_text());self.assertIn('1 · IQ3',chips.nth(1).inner_text())
    hb=page.locator('#app-header').bounding_box();self.assertLessEqual(hb['width'],vp['width']+1)
    if vp['width'] <= 850:
     # Since #125, legacy model/instance chips are deliberately absent from
     # the compact header. Verify its reachable controls instead of waiting
     # for a hidden manual NUMA picker.
     self.assertFalse(chips.first.is_visible())
     self.assertFalse(page.locator('#room-menu-btn').is_visible(),
                      'retired room menu must not consume mobile header space')
     for selector in ('#toggle-agents','#settings-open','#toggle-tools'):
      button=page.locator(selector);self.assertTrue(button.is_visible())
      box=button.bounding_box();self.assertGreaterEqual(box['x'],0);self.assertLessEqual(box['x']+box['width'],vp['width']+1)
     self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
     page.locator('#settings-open').click();page.locator('#settings-dialog').wait_for()
     page.screenshot(path=str(ART/f'numa_compact_{name}.png'))
     page.locator('#settings-close').click();ctx.close();continue
    chips.nth(1).click();sheet=page.locator('#numa-sheet');sheet.wait_for()
    page.screenshot(path=str(ART/f'numa_sheet_{name}.png'))
    sel=sheet.locator('select').nth(1)
    sel.select_option('qwen3.8-flash-next-uncensored')
    sheet.locator('button.primary').nth(1).click()
    modal=page.locator('#mag-modal');modal.wait_for()
    self.assertIn('NUMA 1',page.locator('#mag-modal-text').inner_text());self.assertIn('entladen',page.locator('#mag-modal-text').inner_text())
    page.locator('#mag-modal-cancel').click();modal.wait_for(state='hidden')
    self.assertFalse((tmp/'numa.json').exists(),'cancel must not swap')
    page.keyboard.press('Escape');sheet.wait_for(state='hidden')
    ctx.close()
   # Desktop: confirm, watch loading %, then serving Qwen; assign Bea to node 0.
   ctx=browser.new_context(viewport={'width':1280,'height':860});page=ctx.new_page()
   page.on('pageerror',lambda e:errors.append(str(e)))
   page.goto('http://127.0.0.1:%s/'%ui,wait_until='domcontentloaded')
   chips=page.locator('#model-choice .numa-chip');chips.first.wait_for()
   chips.nth(1).click();sheet=page.locator('#numa-sheet')
   sheet.locator('select').nth(1).select_option('qwen3.8-flash-next-uncensored')
   sheet.locator('button.primary').nth(1).click();page.locator('#mag-modal-ok').click()
   page.wait_for_function("()=>/\\d+%/.test(document.querySelectorAll('#model-choice .numa-chip')[1]?.textContent||'')",timeout=8000)
   page.screenshot(path=str(ART/'numa_loading.png'))
   page.wait_for_function("()=>(document.querySelectorAll('#model-choice .numa-chip')[1]?.textContent||'').includes('Qwen')",timeout=15000)
   seg=sheet.locator('.numa-bots .row',has_text='Bea').locator('button',has_text='N0');seg.click()
   page.wait_for_function("()=>document.querySelector('#numa-sheet .numa-bots')&&[...document.querySelectorAll('#numa-sheet .numa-bots .row')].some(r=>r.textContent.includes('Bea')&&r.querySelector('button[aria-pressed=true]')?.textContent==='N0')",timeout=5000)
   self.assertEqual(json.loads((tmp/'state'/'numa-assign.json').read_text())['b1'],0)
   ctx.close()
   self.assertEqual(errors,[])
  finally:
   pwa.stop_room(proc,log,model);browser.close();pw.stop()
if __name__=='__main__':unittest.main(verbosity=2)
