"""Real Chromium desktop + 375/390/412 mobile clipboard paste regression.
Runs against the checked-in one-file frontend and mocked local API. No S23.
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
import json
import pathlib
import socket
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = pathlib.Path(__file__).resolve().parents[2]
HTML = (ROOT / "mag-room/web/index.html").read_bytes()
STATIC = ROOT / "mag-room/static"

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass
    def do_GET(self):
        p=self.path.split("?",1)[0]
        if p in ("/","/index.html"):
            blob=HTML;mime="text/html; charset=utf-8"
        elif p.startswith("/static/"):
            file=(STATIC / p.removeprefix("/static/")).resolve()
            if not file.is_relative_to(STATIC.resolve()) or not file.is_file():
                self.send_error(404);return
            blob=file.read_bytes()
            mime="text/javascript" if file.suffix==".js" else "text/css"
        elif p=="/api/state":
            blob=json.dumps({"agents":[],"room":[],"files":[],"pending":[],
                "busy":False,"autotalk":False,"queue_paused":False,
                "offline":False,"you":{"role":"owner","name":"Test"}}).encode()
            mime="application/json"
        elif p in ("/api/jobs","/api/queue","/api/files","/api/bots"):
            blob=b'{"jobs":[],"pending":[],"files":[],"agents":[]}';mime="application/json"
        else:
            self.send_error(404);return
        self.send_response(200)
        self.send_header("Content-Type",mime)
        self.send_header("Cache-Control","no-store")
        self.send_header("Content-Length",str(len(blob)))
        self.end_headers()
        self.wfile.write(blob)

class ClipboardImages(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError("pip install playwright; python -m playwright install chromium") from exc
        cls.pw=sync_playwright().start()
        cls.browser=cls.pw.chromium.launch(headless=True)
        cls.server=ThreadingHTTPServer(("127.0.0.1",0),Handler)
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True)
        cls.thread.start()
    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        cls.server.shutdown()
        cls.server.server_close()
    def test_clipboard_paste_and_remove_375_390_412(self):
        for width in (375,390,412,1280):
            with self.subTest(width=width):
                context=self.browser.new_context(viewport={"width":width,"height":800},
                    is_mobile=width<500,has_touch=width<500)
                try:
                    page=context.new_page()
                    page.goto(f"http://127.0.0.1:{self.server.server_port}/",wait_until="domcontentloaded")
                    page.wait_for_selector("#message")
                    # Real Blob, ClipboardEvent and DataTransfer path; matches OS Paste
                    # on macOS/Windows and touch-menu Paste on Android/iOS.
                    page.evaluate("""async () => {
                      const canvas=document.createElement('canvas');canvas.width=16;canvas.height=16;
                      const ctx=canvas.getContext('2d');ctx.fillStyle='red';ctx.fillRect(0,0,16,16);
                      const blob=await new Promise(done=>canvas.toBlob(done,'image/png'));
                      const file=new File([blob],'test.png',{type:'image/png'});
                      const dt=new DataTransfer();dt.items.add(file);
                      document.getElementById('message').dispatchEvent(new ClipboardEvent('paste',{
                        clipboardData:dt,bubbles:true,cancelable:true
                      }));
                    }""")
                    page.wait_for_selector("#photo-preview .photo-card",timeout=8000)
                    self.assertEqual(page.locator("#photo-preview .photo-card img").count(),1)
                    rect=page.locator("#photo-preview .photo-card").bounding_box()
                    self.assertIsNotNone(rect)
                    self.assertGreaterEqual(rect["x"],-1)
                    self.assertLessEqual(rect["x"]+rect["width"],width+1)
                    page.locator("#photo-preview .photo-card button").click()
                    self.assertEqual(page.locator("#photo-preview .photo-card").count(),0)
                finally:
                    context.close()

    def test_native_file_drop_on_chat_and_composer(self):
        for width in (375, 1280):
            with self.subTest(width=width):
                context=self.browser.new_context(viewport={"width":width,"height":800},
                    is_mobile=width<500,has_touch=width<500)
                try:
                    page=context.new_page()
                    page.goto(f"http://127.0.0.1:{self.server.server_port}/",wait_until="domcontentloaded")
                    page.wait_for_selector("#message")
                    # Simulate the OS DataTransfer drop path for two NON-image files
                    # instead of reusing ClipboardEvent or input[type=file].
                    for target in ("#message", ".chat-scroll"):
                        page.evaluate("""(target) => {
                            const dt=new DataTransfer();
                            dt.items.add(new File(['hello'],target.endsWith('message')?'notiz.txt':'zweite.txt',
                                                  {type:'text/plain'}));
                            dt.items.add(new File(['%PDF-1.7'],target.endsWith('message')?'vertrag.pdf':'plan.pdf',
                                                  {type:'application/pdf'}));
                            const node=document.querySelector(target);
                            for(const type of ['dragenter','dragover','drop']){
                                const event=new DragEvent(type,{dataTransfer:dt,bubbles:true,cancelable:true});
                                node.dispatchEvent(event);
                                if(type==='drop'&&!event.defaultPrevented)throw Error('Drop not handled: '+target);
                            }
                        }""",target)
                    page.wait_for_selector("#file-preview .file-chip",timeout=8000)
                    self.assertEqual(page.locator("#file-preview .file-chip").count(),4)
                    self.assertTrue(page.locator("#send-button").is_enabled())
                    self.assertIn("vertrag.pdf",page.locator("#file-preview").inner_text())
                    page.locator("#file-preview .file-chip button").first.click()
                    self.assertEqual(page.locator("#file-preview .file-chip").count(),3)
                finally:
                    context.close()

if __name__ == "__main__":
    unittest.main(verbosity=2)
