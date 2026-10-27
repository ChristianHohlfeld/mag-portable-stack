"""Playwright: an upward wheel detaches, and fast streaming must not move scrollTop."""
# mag-test-timeout: 180
import pathlib as _mag_path
import sys as _mag_sys
_mag_root = _mag_path.Path(__file__).resolve()
while _mag_root != _mag_root.parent and not (_mag_root / 'ops/ci/mag_deadline.py').is_file():
    _mag_root = _mag_root.parent
_mag_sys.path.insert(0, str(_mag_root / 'ops/ci'))
import mag_deadline as _mag_deadline
_mag_deadline.arm(180)
import json, os, pathlib, shutil, socket, subprocess, time, unittest, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading

ROOT = pathlib.Path(__file__).resolve().parents[2]
ART = pathlib.Path('/opt/cursor/artifacts')

def port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]

class Health(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    def log_message(self, *a):
        pass
    def do_GET(self):
        data = b'{"status":"ok"}' if self.path == '/health' else b'{"data":[{"id":"fake"}]}'
        self.send_response(200)
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)
    def do_POST(self):
        n = int(self.headers.get('Content-Length', 0) or 0)
        if n:
            self.rfile.read(n)
        self.send_response(200)
        self.send_header('Content-Length', '0')
        self.end_headers()

class Autoscroll(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import playwright.sync_api  # noqa: F401
        except ImportError:
            raise unittest.SkipTest('playwright is not installed')
        cls.tmp = pathlib.Path('/tmp/mag-autoscroll')
        if cls.tmp.exists():
            shutil.rmtree(cls.tmp)
        state = cls.tmp / 'state'
        state.mkdir(parents=True)
        (cls.tmp / 'netmode.sh').write_text('#!/bin/sh\nexit 0\n')
        os.chmod(cls.tmp / 'netmode.sh', 0o755)
        state.joinpath('agents.json').write_text(json.dumps([
            {'id': 'test', 'name': 'Test', 'vm': '', 'memory': []}
        ]))
        para = 'Ein Absatz, lang genug dass der Verlauf über den Bildschirm läuft. '
        room = []
        for i in range(16):
            role = 'user' if i % 2 == 0 else 'assistant'
            room.append({
                'id': f'{i+1:012x}',
                'role': role,
                'name': 'Du' if role == 'user' else 'Test',
                'content': f'Nachricht {i+1}. ' + para * 5,
                'ts': 1700000000 + i,
            })
        state.joinpath('room.json').write_text(json.dumps(room))
        cls.model = ThreadingHTTPServer(('127.0.0.1', 0), Health)
        threading.Thread(target=cls.model.serve_forever, daemon=True).start()
        cls.ui, cls.bridge = port(), port()
        env = dict(os.environ,
            MAG_OWNER_CANONICAL_LOGIN='chris@github',
            MAG_TEST_ALLOW_LOCAL_OWNER='1',
            MAG_ROOM_STATE=str(state),
            MAG_ROOM_PORT=str(cls.ui),
            MAG_ROOM_BRIDGE_PORT=str(cls.bridge),
            MAG_ROOM_BRIDGE_IP='127.0.0.1',
            MAG_ROOM_BACKEND=f'http://127.0.0.1:{cls.model.server_address[1]}/v1/chat/completions',
            MAG_ROOM_UI=str(ROOT / 'mag-room/web/index.html'),
            MAG_ROOM_STATIC=str(ROOT / 'mag-room/static'),
            MAG_ROOM_AUTOTALK_SLEEP='60',
            MAG_NETMODE=str(cls.tmp / 'netmode.sh'))
        cls.log = open(cls.tmp / 'server.log', 'w')
        cls.proc = subprocess.Popen([str(ROOT / 'build/magroom')], env=env, stdout=cls.log, stderr=cls.log)
        for _ in range(100):
            try:
                urllib.request.urlopen(f'http://127.0.0.1:{cls.ui}/healthz', timeout=0.4).read()
                return
            except Exception:
                time.sleep(0.05)
        raise RuntimeError((cls.tmp / 'server.log').read_text()[-2000:])

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, 'proc', None):
            cls.proc.terminate()
            try:
                cls.proc.wait(timeout=3)
            except Exception:
                cls.proc.kill()
        if getattr(cls, 'log', None):
            cls.log.close()
        if getattr(cls, 'model', None):
            cls.model.shutdown()

    def test_wheel_up_holds_through_three_seconds_of_streaming(self):
        ART.mkdir(parents=True, exist_ok=True)
        video_dir = self.tmp / 'video'
        video_dir.mkdir(exist_ok=True)
        from playwright.sync_api import sync_playwright
        errors = []
        passed = False
        pw_cm = sync_playwright().start()
        browser = pw_cm.chromium.launch(headless=True, args=['--no-sandbox', '--disable-dev-shm-usage'], **({'executable_path': os.environ['MAG_CHROME']} if os.environ.get('MAG_CHROME') else {}))
        context = browser.new_context(
            viewport={'width': 1480, 'height': 1000},
            record_video_dir=str(video_dir),
            record_video_size={'width': 1480, 'height': 1000},
        )
        page = context.new_page()
        page.on('pageerror', lambda err: errors.append(str(err)))
        try:
            page.goto(f'http://127.0.0.1:{self.ui}/', wait_until='domcontentloaded')
            page.wait_for_function(
                """() => {
                  const a = document.getElementById('conversation');
                  const btn = document.getElementById('jump-bottom');
                  if (!a || !btn || !document.body.innerText.includes('Nachricht 16')) return false;
                  return a.scrollHeight > a.clientHeight + 300 && (a.scrollHeight - a.scrollTop - a.clientHeight) < 5 && btn.hidden;
                }""",
                timeout=12000,
            )
            box = page.locator('#conversation').bounding_box()
            self.assertIsNotNone(box)
            page.mouse.move(box['x'] + box['width'] / 2, box['y'] + 80)
            # A tiny trusted wheel, then a short one so the button is on screen.
            page.mouse.wheel(0, -1)
            page.mouse.wheel(0, -80)
            page.wait_for_timeout(120)
            frozen = page.evaluate("document.getElementById('conversation').scrollTop")
            gap = page.evaluate("() => { const a = document.getElementById('conversation'); return a.scrollHeight - a.scrollTop - a.clientHeight }")
            self.assertGreater(gap, 40, 'wheel did not leave the bottom band')
            self.assertFalse(page.locator('#jump-bottom').is_hidden())
            label = page.locator('#jump-bottom').get_attribute('aria-label')
            self.assertTrue(label)

            page.evaluate(
                """() => {
                  const inner = document.querySelector('.conversation-inner');
                  window.__magStream = {n: 0, stop: false};
                  window.__magTick = () => {
                    if (window.__magStream.stop) return;
                    const n = window.__magStream.n++;
                    const p = document.createElement('p');
                    p.textContent = 'Token ' + n + ' ' + 'wort '.repeat(36);
                    inner.appendChild(p);
                    if (n % 5 === 0) {
                      const pre = document.createElement('pre');
                      const code = document.createElement('code');
                      code.textContent = 'fn step(){ return ' + n + '; }\\n'.repeat(4);
                      pre.appendChild(code);
                      inner.appendChild(pre);
                    }
                    requestAnimationFrame(window.__magTick);
                  };
                  requestAnimationFrame(window.__magTick);
                }"""
            )
            samples = []
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                samples.append(page.evaluate("document.getElementById('conversation').scrollTop"))
                time.sleep(0.1)
            drifted = [s for s in samples if abs(s - frozen) > 1]
            self.assertFalse(drifted, f'scrollTop moved while detached: frozen={frozen} samples={samples[:8]}...{samples[-4:]}')
            self.assertGreaterEqual(len(samples), 25)
            self.assertFalse(page.locator('#jump-bottom').is_hidden())
            shot = ART / 'autoscroll_detached.png'
            page.screenshot(path=str(shot))

            page.locator('#jump-bottom').click()
            page.evaluate("window.__magStream.stop = true")
            page.wait_for_function(
                """() => {
                  const a = document.getElementById('conversation');
                  const b = document.getElementById('jump-bottom');
                  return a.scrollHeight - a.scrollTop - a.clientHeight <= 40 && b.hidden;
                }""",
                timeout=3000,
            )
            # Streaming resumes; the pin has to follow the new tokens.
            page.evaluate("window.__magStream.stop = false; requestAnimationFrame(window.__magTick)")
            page.wait_for_timeout(700)
            page.evaluate("window.__magStream.stop = true")
            follow = page.evaluate("""() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(() => {
              const a = document.getElementById('conversation');
              resolve({gap: a.scrollHeight - a.scrollTop - a.clientHeight, hidden: document.getElementById('jump-bottom').hidden});
            })))""")
            self.assertLessEqual(follow['gap'], 40, follow)
            self.assertTrue(follow['hidden'], follow)

            # A late image growing must not be read as the reader scrolling away.
            page.evaluate(
                """() => {
                  const inner = document.querySelector('.conversation-inner');
                  const img = document.createElement('img');
                  img.alt = 'spaeter';
                  img.style.display = 'block';
                  img.style.width = '220px';
                  img.style.height = '4px';
                  inner.appendChild(img);
                  requestAnimationFrame(() => { img.style.height = '420px'; });
                }"""
            )
            page.wait_for_timeout(250)
            still = page.evaluate("() => { const a = document.getElementById('conversation'); return {gap: a.scrollHeight - a.scrollTop - a.clientHeight, hidden: document.getElementById('jump-bottom').hidden} }")
            self.assertLessEqual(still['gap'], 40, still)
            self.assertTrue(still['hidden'], still)

            page.mouse.wheel(0, -200)
            page.wait_for_timeout(150)
            parked = page.evaluate("document.getElementById('conversation').scrollTop")
            self.assertFalse(page.locator('#jump-bottom').is_hidden())
            page.evaluate(
                """() => {
                  const inner = document.querySelector('.conversation-inner');
                  const img = document.createElement('img');
                  img.alt = 'oben';
                  img.style.display = 'block';
                  img.style.width = '180px';
                  img.style.height = '640px';
                  inner.prepend(img);
                }"""
            )
            page.wait_for_timeout(300)
            parked2 = page.evaluate("document.getElementById('conversation').scrollTop")
            self.assertLess(abs(parked2 - parked), 2, f'image above moved the view {parked} -> {parked2}')
            self.assertFalse(page.locator('#jump-bottom').is_hidden())
            self.assertFalse(errors, errors)
            passed = True
        finally:
            try:
                page.evaluate("window.__magStream && (window.__magStream.stop = true)")
            except Exception:
                pass
            video = page.video
            context.close()
            browser.close()
            if passed and video:
                src = pathlib.Path(video.path())
                if src.exists():
                    shutil.copy(src, ART / 'autoscroll_stream.webm')
            pw_cm.stop()

if __name__ == '__main__':
    unittest.main()
