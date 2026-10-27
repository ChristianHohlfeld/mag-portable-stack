"""Playwright: two bots interleave live events. Bot A streams its answer while
bot B's thinking arrives; a refresh that loses the race against a newer one
(mergeActivity without render) and a full render() in between must not move
either reply. Each live reply keeps its first-seen slot, its thinking block
stays inside its own message and no node is duplicated or reordered."""
# mag-test-timeout: 180
import pathlib as _mag_path
import sys as _mag_sys
_mag_root = _mag_path.Path(__file__).resolve()
while _mag_root != _mag_root.parent and not (_mag_root / 'ops/ci/mag_deadline.py').is_file():
    _mag_root = _mag_root.parent
_mag_sys.path.insert(0, str(_mag_root / 'ops/ci'))
import mag_deadline as _mag_deadline
_mag_deadline.arm(180)
import importlib.util, json, pathlib, shutil, unittest
ROOT = pathlib.Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('test_pwa', ROOT / 'mag-room/tests/test_pwa.py')
pwa = importlib.util.module_from_spec(spec); spec.loader.exec_module(pwa)

ORDER = """()=>[...document.querySelectorAll('.conversation-inner > .message.work-live')].map(n=>({
  bot:{Alpha:'a',Beta:'b'}[n.querySelector('.meta strong')?.textContent], think:!!n.querySelector('.thinking'),
  text:n.querySelector('.content')?.textContent||''}))"""


class StreamOrder(unittest.TestCase):
    def test_thinking_of_b_never_jumps_around_streaming_a(self):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self.skipTest('playwright is not installed')
        tmp = pathlib.Path('/tmp/mag-stream-order')
        if tmp.exists():
            shutil.rmtree(tmp)
        (tmp / 'state').mkdir(parents=True)
        (tmp / 'state/agents.json').write_text(json.dumps([
            {'id': 'a', 'name': 'Alpha', 'vm': '', 'memory': []},
            {'id': 'b', 'name': 'Beta', 'vm': '', 'memory': []}]))
        static = tmp / 'static'; shutil.copytree(ROOT / 'mag-room/static', static)
        proc, log, model, ui, bridge = pwa.start_room(tmp, static, 'so')
        pw = sync_playwright().start()
        browser = pw.chromium.launch(channel='chrome', headless=True, args=['--no-sandbox', '--disable-dev-shm-usage'])
        errors = []
        try:
            page = browser.new_page(viewport={'width': 1200, 'height': 900})
            page.on('pageerror', lambda e: errors.append(str(e)))
            page.goto('http://127.0.0.1:%s/' % ui, wait_until='domcontentloaded')
            page.wait_for_function("()=>typeof render==='function'&&state&&state.agents&&state.agents.length===2")
            send = lambda e: page.evaluate("(e)=>{events.onmessage({data:JSON.stringify(e)});return new Promise(r=>requestAnimationFrame(()=>r()))}", e)
            orders = []
            def snap():
                o = page.evaluate(ORDER); orders.append(o); return o
            send({'type': 'bot_start', 'bot': 'a', 'name': 'Alpha'})
            for w in ('Hallo ', 'aus ', 'Alpha '):
                send({'type': 'delta', 'bot': 'a', 'text': w}); snap()
            send({'type': 'bot_start', 'bot': 'b', 'name': 'Beta'}); snap()
            for i in range(6):
                send({'type': 'reasoning', 'bot': 'b', 'text': 'Beta überlegt %d. ' % i}); snap()
                send({'type': 'delta', 'bot': 'a', 'text': 'Wort%d ' % i}); snap()
                if i == 2:
                    # A stale /api/state answer: activities merged, render skipped.
                    page.evaluate("""()=>{for(const [bot,name] of [['b','Beta'],['a','Alpha']])
                      mergeActivity({on:true,bot,name,phase:'think',label:'Denkt nach'})}""")
                if i == 4:
                    page.evaluate('render()')
            send({'type': 'delta', 'bot': 'b', 'text': 'Antwort von Beta'}); snap()
            for n, o in enumerate(orders):
                self.assertEqual([x['bot'] for x in o], ['a'] if n < 3 else ['a', 'b'], o)
            last = orders[-1]
            self.assertFalse(last[0]['think'], 'B thinking leaked into A')
            self.assertNotIn('Beta überlegt', last[0]['text'])
            self.assertIn('Wort5', last[0]['text'])
            self.assertIn('Antwort von Beta', last[1]['text'])
            self.assertNotIn('Beta überlegt', last[1]['text'])
            self.assertEqual(errors, [])
        finally:
            browser.close(); pw.stop()
            proc.terminate(); proc.wait(timeout=5); log.close(); model.shutdown()


if __name__ == '__main__':
    unittest.main()
