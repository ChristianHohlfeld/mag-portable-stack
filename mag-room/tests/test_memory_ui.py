"""Browser regression for the single Memories dialog (mobile and desktop).

A local, stateful API fixture deliberately contains saved memories. The sidebar
must not fetch/render a second view; the retained dialog can read, edit, delete.
No production data, backend, network, or additional runtime services needed.
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
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

ROOT = pathlib.Path(__file__).resolve().parents[2]
HTML = (ROOT / "mag-room/web/index.html").read_bytes()
STATIC = ROOT / "mag-room/static"


class Handler(BaseHTTPRequestHandler):
    memories = [{"text": "Bestehende Erinnerung A"}, {"text": "Bestehende Erinnerung B"}]
    memory_gets = 0

    def log_message(self, *args):
        pass

    def reply(self, code, payload, mime="application/json"):
        data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", mime)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = urlsplit(self.path).path
        if path in ("/", "/index.html"):
            return self.reply(200, HTML, "text/html; charset=utf-8")
        if path.startswith("/static/"):
            file = (STATIC / path.removeprefix("/static/")).resolve()
            if not file.is_relative_to(STATIC.resolve()) or not file.is_file():
                return self.reply(404, {"error": "Not found"})
            typ = ("text/javascript" if file.suffix == ".js" else
                   "text/css" if file.suffix == ".css" else "application/octet-stream")
            return self.reply(200, file.read_bytes(), typ)
        if path == "/api/state":
            return self.reply(200, {
                "agents": [{"id": "test", "name": "DeepSeek Chat", "vm": "",
                            "memory_count": len(type(self).memories)}],
                "room": [], "files": [], "pending": [], "busy": False,
                "autotalk": False, "queue_paused": False, "offline": False,
                "you": {"role": "owner", "name": "Test"}
            })
        if path == "/api/agents/test/memory":
            type(self).memory_gets += 1
            return self.reply(200, type(self).memories)
        if path == "/api/push/vapid":
            return self.reply(200, {"configured": False})
        if path in ("/api/jobs", "/api/queue", "/api/files", "/api/bots"):
            return self.reply(200, {"jobs": [], "pending": [], "files": [], "agents": []})
        return self.reply(404, {"error": "Not found"})

    def do_PUT(self):
        path = urlsplit(self.path).path
        if path == "/api/agents/test/memory/0":
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            items = type(self).memories
            if body.get("old") != items[0]["text"]:
                return self.reply(409, {"error": "Outdated memory"})
            items[0] = {"text": body["text"]}
            return self.reply(200, {"memory": items, "undo": "aabbccdd"})
        return self.reply(404, {"error": "Not found"})

    def do_DELETE(self):
        path = urlsplit(self.path).path
        if path == "/api/agents/test/memory/0":
            items = type(self).memories
            if not items:
                return self.reply(404, {"error": "Not found"})
            items.pop(0)
            return self.reply(200, {"memory": items, "undo": "aabbccdd"})
        return self.reply(404, {"error": "Not found"})


class MemoryDialogBrowser(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright, expect
        cls.expect = expect
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(headless=True)
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()
        cls.server.shutdown()
        cls.server.server_close()

    def test_single_dialog_edit_delete_mobile_and_desktop(self):
        for width in (390, 1280):
            with self.subTest(viewport=width):
                Handler.memories = [{"text": "Bestehende Erinnerung A"},
                                    {"text": "Bestehende Erinnerung B"}]
                Handler.memory_gets = 0
                context = self.browser.new_context(
                    viewport={"width": width, "height": 850},
                    is_mobile=width < 500, has_touch=width < 500,
                    service_workers="block")
                try:
                    page = context.new_page()
                    script_errors, failed_assets, bad_console = [], [], []
                    # Fixture omits the vendor-built xterm bundle; production
                    # deploy stages and validates those pinned assets separately.
                    vendor_only = {'xterm.js', 'xterm.css', 'xterm-addon-fit.js'}
                    page.on('pageerror', lambda error: script_errors.append(str(error)))
                    page.on('response', lambda response: failed_assets.append(
                        (response.status, response.url)) if (
                        response.status >= 400 and
                        (response.url.endswith('/api/state') or
                         '/static/' in response.url and
                         response.url.rsplit('/', 1)[-1].split('?')[0] not in vendor_only)
                    ) else None)
                    page.on('console', lambda msg: bad_console.append(msg.text)
                            if msg.type == 'error' and
                            'Failed to load resource:' not in msg.text else None)
                    page.goto(f"http://127.0.0.1:{self.server.server_port}/",
                              wait_until="domcontentloaded")
                    try:
                        page.locator("#agents .agent").wait_for(state="attached", timeout=5000)
                    except Exception:
                        print("ROOM_STATE_DEBUG", page.evaluate("""() => ({
                          agents: typeof state==='undefined'? 'no state' : state.agents,
                          notice: document.querySelector('#notice')?.textContent,
                          connection: document.querySelector('#connection')?.textContent,
                          cards: document.querySelectorAll('#agents .agent').length,
                          script: document.scripts.length
                        })"""), flush=True)
                        raise
                    self.assertFalse(script_errors, 'Uncaught frontend exceptions: '+repr(script_errors))
                    self.assertFalse(bad_console, 'Frontend console errors: '+repr(bad_console))
                    self.assertFalse(failed_assets, 'Broken app resources/API: '+repr(failed_assets))
                    self.assertFalse(page.locator('#notice').inner_text().strip(),
                                     'Frontend reported an initialization error')
                    self.assertEqual(page.locator("#area-memory").count(), 0)
                    self.assertEqual(page.locator("#memory-areas").count(), 0)
                    self.assertEqual(page.locator(".data-entry").count(), 0)
                    self.assertEqual(Handler.memory_gets, 0,
                                     "sidebar must not prefetch duplicated memory")
                    self.assertEqual(page.locator("#area-room").count(), 1)
                    self.assertEqual(page.locator("#area-work").count(), 1)

                    # Opens the existing bot action, including when the mobile
                    # sidebar is overlaid (native click handler, not a fake DOM).
                    page.evaluate("""() => {
                      const more=document.querySelector('#agents .agent .agent-more');
                      if(!more)throw Error('bot action menu not available');
                      more.click();
                      const btn=[...document.querySelectorAll('.pop-menu [role=menuitem]')].find(b=>b.textContent.trim()==='Erinnerung');
                      if(!btn)throw Error('memory action not available');
                      btn.click();
                    }""")
                    dialog = page.locator("#memory-dialog")
                    dialog.wait_for(state="visible")
                    self.assertIn("DeepSeek Chat", page.locator("#memory-title").inner_text())
                    self.assertEqual(dialog.locator(".mem-row").count(), 2)
                    self.assertEqual(Handler.memory_gets, 1)

                    dialog.locator(".mem-row").first.get_by_role("button", name="Bearbeiten").click()
                    dialog.locator(".mem-row").first.locator("textarea").fill("Bearbeitete Erinnerung")
                    dialog.locator(".mem-row").first.get_by_role("button", name="Speichern").click()
                    dialog.get_by_text("Bearbeitete Erinnerung", exact=True).wait_for()
                    self.assertEqual(Handler.memories[0]["text"], "Bearbeitete Erinnerung")

                    dialog.locator(".mem-row").first.get_by_role("button", name="Löschen").click()
                    page.locator("#purge-dialog").wait_for(state="visible")
                    page.locator("#purge-ok").click()
                    dialog.get_by_text("Bestehende Erinnerung B", exact=True).wait_for()
                    self.expect(dialog.locator(".mem-row")).to_have_count(1)
                    self.assertEqual(Handler.memories, [{"text": "Bestehende Erinnerung B"}])
                    self.assertEqual(page.locator("#area-memory").count(), 0)
                finally:
                    context.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
