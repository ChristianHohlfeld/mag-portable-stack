"""Playwright: the composer owns the keyboard like in Claude/ChatGPT.

Desktop: focused on load, typing anywhere outside an input lands in it,
Ctrl+V outside pastes a screenshot into it, other inputs and dialogs keep
their keys. Phones: no focus on load, so the keyboard does not pop up.
"""
# mag-test-timeout: 180
import pathlib as _mag_path
import sys as _mag_sys
_mag_root = _mag_path.Path(__file__).resolve()
while _mag_root != _mag_root.parent and not (_mag_root / 'ops/ci/mag_deadline.py').is_file():
    _mag_root = _mag_root.parent
_mag_sys.path.insert(0, str(_mag_root / 'ops/ci'))
import mag_deadline as _mag_deadline
_mag_deadline.arm(180)
import pathlib, sys, threading, unittest
from http.server import ThreadingHTTPServer

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from test_image_paste import Handler


class TypeToFocus(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise unittest.SkipTest('playwright is not installed')
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.pw = sync_playwright().start()
        try:
            cls.browser = cls.pw.chromium.launch(args=['--no-sandbox'])
        except Exception:
            cls.browser = cls.pw.chromium.launch(channel='chrome', args=['--no-sandbox'])

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        cls.server.shutdown()
        cls.server.server_close()

    def open(self, **ctx):
        ctx = self.browser.new_context(color_scheme='dark', **ctx)
        page = ctx.new_page()
        page.goto(f'http://127.0.0.1:{self.server.server_port}/')
        page.wait_for_selector('#message')
        page.wait_for_load_state('load')
        return ctx, page

    def active(self, page):
        return page.evaluate('document.activeElement && document.activeElement.id')

    def test_desktop_focus_on_load_and_type_anywhere(self):
        ctx, page = self.open(viewport={'width': 1280, 'height': 800})
        try:
            self.assertEqual(self.active(page), 'message')
            page.evaluate('document.activeElement.blur()')
            page.mouse.click(640, 200)
            self.assertNotEqual(self.active(page), 'message')
            page.keyboard.type('Hallo MAG')
            self.assertEqual(self.active(page), 'message')
            self.assertEqual(page.input_value('#message'), 'Hallo MAG')
            # German layouts: AltGr+Q (Windows: Ctrl+Alt) and Option+L (Mac) type "@".
            for mods in ({'ctrlKey': True, 'altKey': True}, {'altKey': True}):
                page.evaluate('document.activeElement.blur()')
                page.evaluate("""m => document.body.dispatchEvent(new KeyboardEvent('keydown',
                  Object.assign({key:'@',bubbles:true,cancelable:true},m)))""", mods)
                self.assertEqual(self.active(page), 'message')
            page.evaluate('document.activeElement.blur()')
            page.keyboard.press('Control+a')
            page.keyboard.press('Tab')
            page.keyboard.press('Escape')
            self.assertEqual(page.input_value('#message'), 'Hallo MAG', 'shortcuts and navigation keys stay put')
        finally:
            ctx.close()

    def test_ctrl_v_outside_pastes_into_composer(self):
        ctx, page = self.open(viewport={'width': 1280, 'height': 800},
                              permissions=['clipboard-read', 'clipboard-write'])
        try:
            page.evaluate("""async () => {const c=document.createElement('canvas');c.width=40;c.height=20;
              c.getContext('2d').fillRect(0,0,40,20);const b=await new Promise(r=>c.toBlob(r,'image/png'));
              await navigator.clipboard.write([new ClipboardItem({'image/png':b})])}""")
            page.evaluate('document.activeElement.blur()')
            page.keyboard.press('Control+v')
            self.assertEqual(self.active(page), 'message')
            page.wait_for_function('document.querySelectorAll("#photo-preview .photo-card").length===1')
        finally:
            ctx.close()

    def test_other_fields_keep_their_keys(self):
        ctx, page = self.open(viewport={'width': 1280, 'height': 800})
        try:
            page.evaluate("""() => {const i=document.createElement('input');i.id='other';document.body.append(i);i.focus()}""")
            page.keyboard.type('abc')
            self.assertEqual(self.active(page), 'other')
            self.assertEqual(page.input_value('#other'), 'abc')
            self.assertEqual(page.input_value('#message'), '')
        finally:
            ctx.close()

    def test_phone_does_not_focus_on_load(self):
        for name in ('iPhone 13', 'Pixel 7'):
            with self.subTest(device=name):
                d = dict(self.pw.devices[name])
                d.pop('default_browser_type', None)
                ctx, page = self.open(**d)
                try:
                    page.wait_for_timeout(300)
                    self.assertNotEqual(self.active(page), 'message')
                finally:
                    ctx.close()


if __name__ == '__main__':
    unittest.main(verbosity=2)
