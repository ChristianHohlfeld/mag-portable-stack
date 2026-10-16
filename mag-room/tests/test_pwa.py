"""Playwright: a client on build A ends on build B, with no cached copy of A.

Also checks that a Web Push to a subscribed device decrypts (RFC 8291).
"""
import base64, json, os, pathlib, shutil, socket, ssl, subprocess, threading, time, unittest, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = pathlib.Path(__file__).resolve().parents[2]
ART = pathlib.Path('/opt/cursor/artifacts')

def port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]

def b64url(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b'=').decode()

def vapid_env():
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    key = ec.generate_private_key(ec.SECP256R1())
    priv = key.private_numbers().private_value.to_bytes(32, 'big')
    pub = key.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    return {
        'MAG_ROOM_VAPID_PUBLIC': b64url(pub),
        'MAG_ROOM_VAPID_PRIVATE': b64url(priv),
        'MAG_ROOM_VAPID_SUBJECT': 'mailto:mag@localhost',
    }, key

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
        chunks = [b'Hallo ', b'aus dem ', b'Modell.']
        self.send_response(200)
        self.send_header('Transfer-Encoding', 'chunked')
        self.end_headers()
        for piece in chunks:
            event = json.dumps({'choices': [{'delta': {'content': piece.decode()}}]}).encode()
            payload = b'data: ' + event + b'\n\n'
            self.wfile.write(('%x\r\n' % len(payload)).encode() + payload + b'\r\n')
        end = b'data: [DONE]\n\n'
        self.wfile.write(('%x\r\n' % len(end)).encode() + end + b'\r\n0\r\n\r\n')
        self.wfile.flush()

class PushSink(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    hits = []
    def log_message(self, *a):
        pass
    def do_POST(self):
        n = int(self.headers.get('Content-Length', 0) or 0)
        body = self.rfile.read(n)
        type(self).hits.append({'headers': {k.lower(): v for k, v in self.headers.items()}, 'body': body})
        self.send_response(201)
        self.send_header('Content-Length', '0')
        self.end_headers()

def wait_health(ui, log_path):
    for _ in range(100):
        try:
            urllib.request.urlopen('http://127.0.0.1:%s/healthz' % ui, timeout=0.4).read()
            return
        except Exception:
            time.sleep(0.05)
    raise RuntimeError(pathlib.Path(log_path).read_text()[-2000:])

def start_room(tmp, static, version, extra=None, ui=None, bridge=None):
    state = tmp / 'state'
    state.mkdir(parents=True, exist_ok=True)
    if not (state / 'agents.json').exists():
        (state / 'agents.json').write_text(json.dumps([
            {'id': 'test', 'name': 'Test', 'vm': '', 'memory': []}
        ]))
    net = tmp / 'netmode.sh'
    if not net.exists():
        net.write_text('#!/bin/sh\nexit 0\n')
        net.chmod(0o755)
    ui = ui or port()
    bridge = bridge or port()
    model = ThreadingHTTPServer(('127.0.0.1', 0), Health)
    threading.Thread(target=model.serve_forever, daemon=True).start()
    env = dict(os.environ,
        MAG_ROOM_STATE=str(state),
        MAG_ROOM_PORT=str(ui),
        MAG_ROOM_BRIDGE_PORT=str(bridge),
        MAG_ROOM_BRIDGE_IP='127.0.0.1',
        MAG_ROOM_BACKEND='http://127.0.0.1:%s/v1/chat/completions' % model.server_address[1],
        MAG_ROOM_UI=str(ROOT / 'mag-room/web/index.html'),
        MAG_ROOM_STATIC=str(static),
        MAG_ROOM_VERSION=version,
        MAG_ROOM_AUTOTALK_SLEEP='60',
        MAG_ROOM_MODEL_STATUS=str(tmp / 'model-status.json'),
        MAG_NETMODE=str(net))
    if extra:
        env.update(extra)
    log = open(tmp / 'server.log', 'a')
    proc = subprocess.Popen([str(ROOT / 'build/magroom')], env=env, stdout=log, stderr=log)
    try:
        wait_health(ui, tmp / 'server.log')
    except Exception:
        proc.kill()
        log.close()
        model.shutdown()
        raise
    return proc, log, model, ui, bridge

def stop_room(proc, log, model):
    if proc and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except Exception:
            proc.kill()
    if log:
        log.close()
    if model:
        model.shutdown()

def token_of(ui):
    import re
    html = urllib.request.urlopen('http://127.0.0.1:%s/' % ui, timeout=10).read().decode()
    return re.search(r'name="mag-token" content="([^"]+)"', html).group(1)

def api(ui, path, body=None, method=None):
    data = None if body is None else json.dumps(body).encode()
    headers = {'Content-Type': 'application/json', 'X-MAG-Token': token_of(ui)} if data is not None else {}
    req = urllib.request.Request('http://127.0.0.1:%s%s' % (ui, path), data=data, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=10) as r:
        raw = r.read()
        return json.loads(raw) if raw else {}

class Pwa(unittest.TestCase):
    def test_version_a_then_b_drops_old_assets(self):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self.skipTest('playwright is not installed')
        ART.mkdir(parents=True, exist_ok=True)
        tmp = pathlib.Path('/tmp/mag-pwa-ab')
        if tmp.exists():
            shutil.rmtree(tmp)
        static = tmp / 'static'
        shutil.copytree(ROOT / 'mag-room/static', static)
        probe = static / 'pwa-probe.txt'
        probe.write_text('asset-a\n')
        keys, _ = vapid_env()
        proc, log, model, ui, bridge = start_room(tmp, static, 'pwa-a', keys)
        errors = []
        pw = sync_playwright().start()
        browser = pw.chromium.launch(channel='chrome', headless=True, args=['--no-sandbox', '--disable-dev-shm-usage'])
        context = browser.new_context(viewport={'width': 1480, 'height': 1000}, permissions=['notifications'])
        page = context.new_page()
        page.on('pageerror', lambda err: errors.append(str(err)))
        try:
            page.goto('http://127.0.0.1:%s/' % ui, wait_until='domcontentloaded')
            page.wait_for_function('() => navigator.serviceWorker && navigator.serviceWorker.controller', timeout=15000)
            got = page.evaluate("""async () => {
              const res = await fetch('/static/pwa-probe.txt?v=pwa-a');
              const text = await res.text();
              const keys = await caches.keys();
              let cached = false;
              for (const name of keys) {
                const cache = await caches.open(name);
                for (const req of await cache.keys()) {
                  const body = await (await cache.match(req)).text();
                  if (body.includes('asset-a')) cached = true;
                }
              }
              return {text, keys, cached};
            }""")
            self.assertIn('asset-a', got['text'])
            self.assertTrue(got['cached'], got)
            page.locator('#pwa-install').scroll_into_view_if_needed()
            page.locator('#pwa-install').wait_for(state='visible')
            page.locator('#pwa-install-block').screenshot(path=str(ART / 'pwa_install.png'))
            page.locator('#pwa-notify').scroll_into_view_if_needed()
            page.wait_for_function("() => { const b = document.getElementById('pwa-push'); return b && !b.disabled && !b.hidden; }")
            page.locator('#pwa-notify').screenshot(path=str(ART / 'pwa_push_optin.png'))
            page.evaluate("""async () => {
              const reg = await navigator.serviceWorker.ready;
              await reg.showNotification('Quellen geprüft', {
                body: 'Die drei Treffer sind gespeichert.',
                tag: 'job-demo',
                icon: '/static/icon-192.png?v=pwa-a'
              });
            }""")
            page.locator('#message').fill('noch nicht senden')
            stop_room(proc, log, model)
            proc = log = model = None
            probe.write_text('asset-b\n')
            proc, log, model, ui_b, bridge_b = start_room(tmp, static, 'pwa-b', keys, ui=ui, bridge=bridge)
            self.assertEqual(ui, ui_b)
            self.assertEqual(bridge, bridge_b)
            page.evaluate("() => window.dispatchEvent(new Event('focus'))")
            page.locator('#update-toast').wait_for(state='visible', timeout=20000)
            self.assertIn('Neue Version – aktualisieren', page.locator('#update-toast').inner_text())
            self.assertEqual(page.locator('meta[name=mag-version]').get_attribute('content'), 'pwa-a')
            page.screenshot(path=str(ART / 'pwa_update_toast.png'))
            page.locator('#update-now').click()
            page.wait_for_function("""async () => {
              const meta = document.querySelector('meta[name=mag-version]');
              if (!meta || meta.content !== 'pwa-b') return false;
              const ctrl = navigator.serviceWorker && navigator.serviceWorker.controller;
              if (!ctrl) return false;
              const version = await new Promise((resolve) => {
                const onMsg = (event) => {
                  if (event.data && event.data.version) {
                    navigator.serviceWorker.removeEventListener('message', onMsg);
                    resolve(event.data.version);
                  }
                };
                navigator.serviceWorker.addEventListener('message', onMsg);
                ctrl.postMessage({type: 'VERSION'});
                setTimeout(() => resolve(''), 1000);
              });
              if (version !== 'pwa-b') return false;
              const names = await caches.keys();
              if (names.some((name) => name.includes('pwa-a'))) return false;
              for (const name of names) {
                const cache = await caches.open(name);
                for (const req of await cache.keys()) {
                  const url = new URL(req.url);
                  if (url.pathname.startsWith('/api') || url.pathname === '/' || url.pathname === '/index.html')
                    return false;
                  const body = await (await cache.match(req)).text();
                  if (body.includes('asset-a')) return false;
                }
              }
              const live = await (await fetch('/api/version', {cache: 'no-store'})).json();
              if (live.version !== 'pwa-b') return false;
              const probe = await (await fetch('/static/pwa-probe.txt?v=pwa-b', {cache: 'no-store'})).text();
              if (!probe.includes('asset-b')) return false;
              const state = await (await fetch('/api/state', {cache: 'no-store'})).json();
              if (!state || !('room' in state)) return false;
              const matched = await caches.match('/api/state');
              if (matched) return false;
              return true;
            }""", timeout=20000)
            self.assertEqual(errors, [])
            context.set_offline(True)
            offline = page.goto('http://127.0.0.1:%s/' % ui, wait_until='domcontentloaded')
            self.assertIn('MAG ist offline', page.content())
            self.assertNotIn('asset-a', page.content())
            self.assertNotIn('Schreibe eine Nachricht', page.content())
            if offline:
                self.assertGreaterEqual(offline.status, 200)
        finally:
            context.close()
            browser.close()
            pw.stop()
            stop_room(proc, log, model)

    def test_push_roundtrip_and_prefs(self):
        import http_ece
        from cryptography.hazmat.primitives.asymmetric import ec
        tmp = pathlib.Path('/tmp/mag-pwa-push')
        if tmp.exists():
            shutil.rmtree(tmp)
        static = tmp / 'static'
        shutil.copytree(ROOT / 'mag-room/static', static)
        cert = tmp / 'push.pem'
        key = tmp / 'push.key'
        subprocess.check_call([
            'openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-keyout', str(key),
            '-out', str(cert), '-days', '1', '-nodes', '-subj', '/CN=127.0.0.1',
            '-addext', 'subjectAltName=IP:127.0.0.1'
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        PushSink.hits = []
        httpd = ThreadingHTTPServer(('127.0.0.1', 0), PushSink)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(str(cert), str(key))
        httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        sub = ec.generate_private_key(ec.SECP256R1())
        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
        sub_pub = sub.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
        auth = os.urandom(16)
        keys, _ = vapid_env()
        keys['SSL_CERT_FILE'] = str(cert)
        keys['MAG_ROOM_PUSH_RUN_S'] = '0'
        proc, log, model, ui, _bridge = start_room(tmp, static, 'push-a', keys)
        try:
            endpoint = 'https://127.0.0.1:%s/push' % httpd.server_address[1]
            api(ui, '/api/push/subscribe', {
                'endpoint': endpoint,
                'keys': {'p256dh': b64url(sub_pub), 'auth': b64url(auth)},
                'types': {'job': True, 'run': True, 'error': True},
            })
            api(ui, '/api/message', {'text': 'Ping', 'bots': ['test']})
            deadline = time.time() + 15
            while time.time() < deadline and not PushSink.hits:
                time.sleep(0.1)
            self.assertTrue(PushSink.hits, (tmp / 'server.log').read_text()[-2000:])
            hit = PushSink.hits[0]
            self.assertTrue(hit['headers']['authorization'].startswith('vapid t='))
            self.assertIn(keys['MAG_ROOM_VAPID_PUBLIC'], hit['headers']['authorization'])
            self.assertEqual(hit['headers']['content-encoding'], 'aes128gcm')
            plain = http_ece.decrypt(hit['body'], private_key=sub, auth_secret=auth, version='aes128gcm')
            note = json.loads(plain)
            self.assertEqual(note['kind'], 'run')
            self.assertEqual(note['title'], 'Test')
            self.assertIn('Hallo aus dem Modell', note['body'])
            self.assertEqual(note['url'], '/')
            api(ui, '/api/push/prefs', {'endpoint': endpoint, 'types': {'job': True, 'run': False, 'error': True}}, 'PUT')
            before = len(PushSink.hits)
            for _ in range(50):
                if not api(ui, '/api/state')['busy']:
                    break
                time.sleep(0.05)
            api(ui, '/api/message', {'text': 'Noch eins', 'bots': ['test']})
            time.sleep(2.5)
            self.assertEqual(len(PushSink.hits), before)
            api(ui, '/api/push/subscribe', {'endpoint': endpoint}, 'DELETE')
            api(ui, '/api/message', {'text': 'Nach abmelden', 'bots': ['test']})
            time.sleep(1.5)
            self.assertEqual(len(PushSink.hits), before)
        finally:
            stop_room(proc, log, model)
            httpd.shutdown()

    def test_error_push(self):
        import http_ece
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
        tmp = pathlib.Path('/tmp/mag-pwa-error')
        if tmp.exists():
            shutil.rmtree(tmp)
        static = tmp / 'static'
        shutil.copytree(ROOT / 'mag-room/static', static)
        (tmp / 'model-status.json').write_text(json.dumps({'state': 'failed', 'detail': 'sha'}))
        cert = tmp / 'push.pem'
        key = tmp / 'push.key'
        subprocess.check_call([
            'openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-keyout', str(key),
            '-out', str(cert), '-days', '1', '-nodes', '-subj', '/CN=127.0.0.1',
            '-addext', 'subjectAltName=IP:127.0.0.1'
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        PushSink.hits = []
        httpd = ThreadingHTTPServer(('127.0.0.1', 0), PushSink)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(str(cert), str(key))
        httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        sub = ec.generate_private_key(ec.SECP256R1())
        sub_pub = sub.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
        auth = os.urandom(16)
        keys, _ = vapid_env()
        keys['SSL_CERT_FILE'] = str(cert)
        # Closed backend: health fails, status file says failed, error is immediate after the probe.
        keys['MAG_ROOM_BACKEND'] = 'http://127.0.0.1:1/v1/chat/completions'
        proc, log, model, ui, _bridge = start_room(tmp, static, 'push-err', keys)
        try:
            endpoint = 'https://127.0.0.1:%s/push' % httpd.server_address[1]
            api(ui, '/api/push/subscribe', {
                'endpoint': endpoint,
                'keys': {'p256dh': b64url(sub_pub), 'auth': b64url(auth)},
                'types': {'job': False, 'run': False, 'error': True},
            })
            api(ui, '/api/message', {'text': 'Bitte', 'bots': ['test']})
            deadline = time.time() + 12
            while time.time() < deadline and not PushSink.hits:
                time.sleep(0.1)
            self.assertTrue(PushSink.hits, (tmp / 'server.log').read_text()[-2000:])
            plain = http_ece.decrypt(PushSink.hits[0]['body'], private_key=sub, auth_secret=auth, version='aes128gcm')
            note = json.loads(plain)
            self.assertEqual(note['kind'], 'error')
            self.assertIn('Modell', note['body'])
            self.assertTrue(note['url'].startswith('/'))
        finally:
            stop_room(proc, log, model)
            httpd.shutdown()

if __name__ == '__main__':
    unittest.main(verbosity=2)
