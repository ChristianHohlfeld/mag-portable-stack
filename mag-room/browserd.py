#!/usr/bin/env python3
"""Persistent browser daemon for MAG bot VMs."""
import json
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import sync_playwright

PORT = int(os.environ.get('BROWSERD_PORT', '9225'))
HOME_URL = os.environ.get('BROWSERD_HOME', 'about:blank')
VIEW_W, VIEW_H = int(os.environ.get('BROWSERD_W', '1440')), int(os.environ.get('BROWSERD_H', '900'))

_executor = ThreadPoolExecutor(max_workers=1)
_state = {'page': None}


def _init():
    p = sync_playwright().start()
    browser = p.chromium.launch(headless=True, args=[
        '--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu', '--hide-scrollbars'])
    context = browser.new_context(viewport={'width': VIEW_W, 'height': VIEW_H}, locale='de-DE')
    page = context.new_page()
    page.set_default_timeout(20000)
    try:
        page.goto(HOME_URL, wait_until='load', timeout=30000)
    except Exception:
        pass
    _state['page'] = page
    _state['playwright'] = p
    _state['browser'] = browser


def run(fn):
    return _executor.submit(fn).result()


def snapshot():
    page = _state['page']
    return {'url': page.url, 'title': page.title()}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, ctype, body):
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Connection', 'close')
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code, val):
        self._send(code, 'application/json', json.dumps(val, ensure_ascii=False).encode())

    def do_GET(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        path = parsed.path
        try:
            if path == '/state':
                return self._json(200, run(snapshot))
            if path == '/screenshot.png':
                data = run(lambda: _state['page'].screenshot(type='png'))
                return self._send(200, 'image/png', data)
            if path == '/read':
                def read():
                    page = _state['page']
                    return {'url': page.url, 'title': page.title(), 'text': page.inner_text('body')[:8000]}
                return self._json(200, run(read))
            if path == '/navigate':
                url = (query.get('url') or [''])[0]
                if url and not url.startswith(('http://', 'https://')):
                    url = 'https://' + url
                run(lambda: _state['page'].goto(url, wait_until='load', timeout=45000))
                return self._json(200, run(snapshot))
            if path == '/back':
                run(lambda: _state['page'].go_back(wait_until='load'))
                return self._json(200, run(snapshot))
            if path == '/forward':
                run(lambda: _state['page'].go_forward(wait_until='load'))
                return self._json(200, run(snapshot))
            if path == '/reload':
                run(lambda: _state['page'].reload(wait_until='load'))
                return self._json(200, run(snapshot))
            return self._json(404, {'error': 'not found'})
        except Exception as e:
            return self._json(500, {'error': str(e)})

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        length = int(self.headers.get('Content-Length', '0'))
        try:
            data = json.loads(self.rfile.read(length) or b'{}')
        except ValueError:
            data = {}
        try:
            if path == '/click':
                sel = data.get('selector'); text = data.get('text')
                if sel:
                    run(lambda: _state['page'].click(sel, timeout=15000))
                elif text:
                    run(lambda: _state['page'].get_by_text(str(text), exact=False).first.click(timeout=15000))
                else:
                    return self._json(400, {'error': 'selector oder text fehlt'})
                return self._json(200, run(snapshot))
            if path == '/type':
                sel = data.get('selector'); text = str(data.get('text', ''))
                if sel:
                    run(lambda: _state['page'].fill(sel, text))
                    if data.get('enter'):
                        run(lambda: _state['page'].press(sel, 'Enter'))
                else:
                    run(lambda: _state['page'].keyboard.type(text))
                    if data.get('enter'):
                        run(lambda: _state['page'].keyboard.press('Enter'))
                return self._json(200, run(snapshot))
            if path == '/key':
                run(lambda: _state['page'].keyboard.press(str(data.get('key', 'Enter'))))
                return self._json(200, run(snapshot))
            if path == '/scroll':
                run(lambda: _state['page'].mouse.wheel(0, int(data.get('dy', 600))))
                return self._json(200, run(snapshot))
            return self._json(404, {'error': 'not found'})
        except Exception as e:
            return self._json(500, {'error': str(e)})


def daemonize(log_path):
    if os.fork() > 0:
        os._exit(0)
    os.setsid()
    if os.fork() > 0:
        os._exit(0)
    sys.stdin.flush() if hasattr(sys.stdin, 'flush') else None
    devnull = os.open(os.devnull, os.O_RDONLY)
    log = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    os.dup2(devnull, 0)
    os.dup2(log, 1)
    os.dup2(log, 2)


if __name__ == '__main__':
    if '--daemon' in sys.argv:
        daemonize(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'browserd.log'))
    _executor.submit(_init).result()
    print('browserd on :%d' % PORT, flush=True)
    ThreadingHTTPServer(('0.0.0.0', PORT), Handler).serve_forever()
