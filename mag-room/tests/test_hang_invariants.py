# mag-test-timeout: 90
import pathlib as _mag_path
import sys as _mag_sys
_mag_root = _mag_path.Path(__file__).resolve()
while _mag_root != _mag_root.parent and not (_mag_root / 'ops/ci/mag_deadline.py').is_file():
    _mag_root = _mag_root.parent
_mag_sys.path.insert(0, str(_mag_root / 'ops/ci'))
import mag_deadline as _mag_deadline
_mag_deadline.arm(90)
"""Fault injection for the room, the audit gateway and a stuck upstream.

Every listener is on 127.0.0.1 with an ephemeral port. Nothing here waits on
a real model slot. A stalled stream, a stream that never ends, HTTP 429, a
slot that stays busy, and Stop each have to leave the room idle inside a
fixed bound, with the queue empty. The gateway has to drop a silent upstream
while the client is still connected.
"""
import json
import os
import pathlib
import select
import signal
import socket
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

for _key in ('http_proxy', 'https_proxy', 'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'all_proxy'):
    os.environ.pop(_key, None)
os.environ['NO_PROXY'] = '*'
os.environ['no_proxy'] = '*'

ROOT = pathlib.Path(__file__).resolve().parents[2]
BIN = ROOT / 'build/magroom'
GATEWAY = ROOT / 'build/mag-audit-gateway'


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *args):
        return

    def do_GET(self):
        if self.path.startswith('/slots'):
            body = b'[{"id":0,"is_processing":true,"n_prompt_tokens":10452}]'
        elif self.path.startswith('/health'):
            body = b'{"status":"ok"}'
        else:
            body = b'{"data":[{"id":"deepseek-v4-flash-0731"}]}'
        self._bytes(200, body)

    def do_POST(self):
        size = int(self.headers.get('Content-Length') or 0)
        self.rfile.read(size)
        if not self.path.startswith('/v1/chat/completions'):
            self._bytes(200, b'{"ok":true}')
            return
        with self.server.lock:
            self.server.calls += 1
            kind = self.server.scripts.pop(0) if self.server.scripts else 'ok'
        try:
            if kind == 'stall':
                self._headers()
                self._until_close()
            elif kind == 'trickle':
                self._headers()
                self._trickle()
            elif kind == '429':
                self._bytes(429, b'{"error":"rate"}')
            else:
                text = 'ok' if kind == 'ok' else kind.split(':', 1)[1]
                sse = (
                    'data: ' + json.dumps({'choices': [{'delta': {'content': text}}]}) + '\n\n'
                    'data: [DONE]\n\n'
                ).encode()
                self._bytes(200, sse, 'text/event-stream')
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            self.server.disconnects.append(time.monotonic())

    def _headers(self):
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Transfer-Encoding', 'chunked')
        self.end_headers()

    def _bytes(self, code, body, content_type='application/json'):
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Connection', 'close')
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True

    def _chunk(self, payload):
        self.wfile.write(('%x\r\n' % len(payload)).encode() + payload + b'\r\n')
        self.wfile.flush()

    def _until_close(self):
        end = time.monotonic() + 20
        while time.monotonic() < end:
            if self._peer_gone():
                self.server.disconnects.append(time.monotonic())
                return
        return

    def _trickle(self):
        end = time.monotonic() + 20
        token = b'data: {"choices":[{"delta":{"content":"x"}}]}\n\n'
        while time.monotonic() < end:
            if self._peer_gone():
                self.server.disconnects.append(time.monotonic())
                return
            self._chunk(token)
            time.sleep(0.2)

    def _peer_gone(self):
        try:
            ready, _, _ = select.select([self.connection], [], [], 0.05)
            if not ready:
                return False
            return self.connection.recv(1, socket.MSG_PEEK) == b''
        except OSError:
            return True


class Room:
    def __init__(self, scripts, extra):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = pathlib.Path(self.tmp.name)
        state = self.dir / 'state'
        state.mkdir()
        (state / 'agents.json').write_text(json.dumps([
            {'id': 'a', 'name': 'Alpha', 'vm': '', 'memory': []},
        ]), encoding='utf-8')
        netmode = self.dir / 'netmode.sh'
        netmode.write_text('#!/bin/sh\nexit 0\n', encoding='utf-8')
        netmode.chmod(0o755)
        self.http = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.http.scripts = list(scripts)
        self.http.calls = 0
        self.http.disconnects = []
        self.http.lock = threading.Lock()
        threading.Thread(target=self.http.serve_forever, daemon=True).start()
        ui, bridge = free_port(), free_port()
        env = os.environ.copy()
        for key in list(env):
            if key.startswith('MAG_ROOM_MODEL'):
                env.pop(key)
        env.update({
            'MAG_SECONDARY_MODEL': 'iq3',
            'MAG_OWNER_CANONICAL_LOGIN': 'chris@github',
            'MAG_TEST_ALLOW_LOCAL_OWNER': '1',
            'MAG_ROOM_STATE': str(state),
            'MAG_ROOM_PORT': str(ui),
            'MAG_ROOM_BRIDGE_PORT': str(bridge),
            'MAG_ROOM_BRIDGE_IP': '127.0.0.1',
            'MAG_ROOM_BACKEND': f'http://127.0.0.1:{self.http.server_port}/v1/chat/completions',
            'MAG_ROOM_UI': str(ROOT / 'mag-room/web/index.html'),
            'MAG_ROOM_STATIC': str(ROOT / 'mag-room/static'),
            'MAG_ROOM_MODEL_STATUS': str(self.dir / 'model-status.json'),
            'MAG_NETMODE': str(netmode),
            'MAG_TEST_NO_MEMGIT': '1',
            'MAG_TEST_NO_CLEAR_BACKUP': '1',
            'MAG_ROOM_MCP_AUTO': '0',
            'MAG_ROOM_PREWARM': '0',
            'MAG_ROOM_STAGGER': 'off',
        })
        env.update(extra)
        self.log = open(self.dir / 'server.log', 'w', encoding='utf-8')
        self.proc = subprocess.Popen([str(BIN)], env=env, stdout=self.log, stderr=self.log, start_new_session=True)
        self.ui = ui
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                break
            try:
                urllib.request.urlopen(f'http://127.0.0.1:{ui}/healthz', timeout=1).read()
                break
            except Exception:
                time.sleep(0.05)
        else:
            raise RuntimeError('room did not start\n' + self._log())
        if self.proc.poll() is not None:
            raise RuntimeError('room exited\n' + self._log())
        html = urllib.request.urlopen(f'http://127.0.0.1:{ui}/', timeout=5).read().decode()
        self.token = html.split('name="mag-token" content="', 1)[1].split('"', 1)[0]

    def _log(self):
        self.log.flush()
        return (self.dir / 'server.log').read_text(encoding='utf-8', errors='replace')[-4000:]

    def close(self):
        if self.proc.poll() is None:
            try:
                os.killpg(self.proc.pid, signal.SIGTERM)
            except OSError:
                self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(self.proc.pid, signal.SIGKILL)
                self.proc.wait(timeout=5)
        self.log.close()
        self.http.shutdown()
        self.http.server_close()
        self.tmp.cleanup()

    def api(self, path, body=None, method=None):
        data = json.dumps(body).encode() if body is not None else None
        headers = {}
        if data is not None or method:
            headers['X-MAG-Token'] = self.token
        if data is not None:
            headers['Content-Type'] = 'application/json'
        req = urllib.request.Request(f'http://127.0.0.1:{self.ui}{path}', data=data, method=method, headers=headers)
        with urllib.request.urlopen(req, timeout=5) as response:
            return json.loads(response.read())

    def until(self, pred, timeout, what):
        end = time.monotonic() + timeout
        last = None
        while time.monotonic() < end:
            last = self.api('/api/state')
            if pred(last):
                return last
            time.sleep(0.05)
        raise AssertionError(
            f'{what} not reached; busy={last and last.get("busy")} '
            f'pending={len((last or {}).get("pending") or [])} '
            f'room={[m.get("content") for m in (last or {}).get("room") or []][-6:]}\n{self._log()}'
        )


def assistants(state):
    return [m.get('content') or '' for m in state.get('room') or [] if m.get('role') == 'assistant']


def idle_and_drained(state, count):
    return (not state.get('busy')) and not state.get('pending') and len(assistants(state)) >= count


class HangInvariants(unittest.TestCase):
    def setUp(self):
        if not BIN.exists():
            self.skipTest('build/magroom missing')
        self.room = None

    def tearDown(self):
        if self.room is not None:
            self.room.close()

    def _say(self, text):
        self.room.api('/api/message', {'text': text, 'bots': ['a']})

    def test_stalled_upstream_clears_busy(self):
        self.room = Room(['stall'], {'MAG_ROOM_STALL_S': '2', 'MAG_ROOM_TURN_DEADLINE_S': '120'})
        started = time.monotonic()
        self._say('bleib still')
        state = self.room.until(lambda s: idle_and_drained(s, 1), 8, 'idle after stall')
        self.assertLess(time.monotonic() - started, 8)
        self.assertIn('Keine Rückmeldung', assistants(state)[-1])

    def test_endless_stream_drains_the_queue(self):
        self.room = Room(['trickle', 'trickle'], {'MAG_ROOM_STALL_S': '30', 'MAG_ROOM_TURN_DEADLINE_S': '3'})
        started = time.monotonic()
        self._say('erste')
        self._say('zweite')
        state = self.room.until(lambda s: idle_and_drained(s, 2), 20, 'both turns ended')
        self.assertLess(time.monotonic() - started, 20)
        for text in assistants(state)[-2:]:
            self.assertIn('Modell-Zeitlimit', text)

    def test_http_429_then_the_next_message(self):
        self.room = Room(['429', 'text:danach'], {'MAG_ROOM_STALL_S': '10', 'MAG_ROOM_TURN_DEADLINE_S': '30'})
        self._say('zu viel')
        self._say('nochmal')
        state = self.room.until(lambda s: idle_and_drained(s, 2), 15, '429 then answer')
        texts = assistants(state)
        self.assertIn('429', texts[0])
        self.assertIn('danach', texts[1])

    def test_occupied_slot_does_not_stick_the_queue(self):
        self.room = Room(['stall', 'text:ok'], {'MAG_ROOM_STALL_S': '2', 'MAG_ROOM_TURN_DEADLINE_S': '30'})
        started = time.monotonic()
        self._say('slot belegt')
        self.room.until(lambda s: s.get('busy') or self.room.http.calls, 5, 'first turn started')
        self._say('danach frei')
        state = self.room.until(lambda s: idle_and_drained(s, 2), 15, 'queue after occupied slot')
        self.assertLess(time.monotonic() - started, 15)
        self.assertIn('Keine Rückmeldung', assistants(state)[0])
        self.assertIn('ok', assistants(state)[1])

    def test_stop_frees_the_room(self):
        self.room = Room(['stall'], {'MAG_ROOM_STALL_S': '30', 'MAG_ROOM_TURN_DEADLINE_S': '60'})
        self._say('stopp mich')
        self.room.until(lambda s: self.room.http.calls >= 1, 5, 'model accepted the turn')
        started = time.monotonic()
        cancelled = self.room.api('/api/cancel', {})
        self.assertTrue(cancelled.get('cancelled'))
        self.room.until(lambda s: not s.get('busy'), 4, 'idle after stop')
        self.assertLess(time.monotonic() - started, 4)
        end = time.monotonic() + 2
        while time.monotonic() < end and not self.room.http.disconnects:
            time.sleep(0.05)
        self.assertTrue(self.room.http.disconnects, 'Stop must close the model socket')


class GatewayIdle(unittest.TestCase):
    def test_silent_upstream_is_closed_while_the_client_stays(self):
        if not GATEWAY.exists():
            self.skipTest('build/mag-audit-gateway missing')
        upstream = socket.socket()
        upstream.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        upstream.bind(('127.0.0.1', 0))
        upstream.listen(1)
        upstream.settimeout(8)
        closed = []

        def accept_one():
            conn, _ = upstream.accept()
            conn.settimeout(0.2)
            started = time.monotonic()
            try:
                while time.monotonic() - started < 12:
                    try:
                        data = conn.recv(1)
                    except socket.timeout:
                        continue
                    if data == b'':
                        closed.append(time.monotonic() - started)
                        return
            finally:
                conn.close()

        threading.Thread(target=accept_one, daemon=True).start()
        other = free_port()
        listen = free_port()
        tmp = tempfile.TemporaryDirectory()
        env = os.environ.copy()
        for key in list(env):
            if key.startswith('MAG_ROOM_MODEL'):
                env.pop(key)
        env.update({
            'MAG_AUDIT_LISTEN_PORT': str(listen),
            'MAG_MODEL_Q4_PORT': str(upstream.getsockname()[1]),
            'MAG_MODEL_IQ3_PORT': str(other),
            'MAG_SECONDARY_MODEL': 'iq3',
            'MAG_GATEWAY_IDLE_S': '2',
            'MAG_GATEWAY_DEADLINE_S': '5',
            'MAG_GATEWAY_READ_S': '5',
            'MAG_AUDIT_LOG_DIR': str(pathlib.Path(tmp.name) / 'logs'),
            'MAG_AUDIT_SECRETS_FILE': str(pathlib.Path(tmp.name) / 'missing'),
            'MAG_MODEL_CONTROL_SOCKET': str(pathlib.Path(tmp.name) / 'control.sock'),
            'MAG_MODEL_SUPERVISOR_SOCKET': str(pathlib.Path(tmp.name) / 'supervisor.sock'),
        })
        proc = subprocess.Popen(
            [str(GATEWAY)], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, start_new_session=True)
        try:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if proc.poll() is not None:
                    raise AssertionError(proc.stderr.read().decode(errors='replace'))
                try:
                    probe = socket.create_connection(('127.0.0.1', listen), 0.2)
                    probe.close()
                    break
                except OSError:
                    time.sleep(0.05)
            else:
                raise AssertionError('gateway did not listen')
            body = b'{"model":"deepseek-v4-flash-0731","messages":[{"role":"user","content":"hi"}]}'
            request = (
                'POST /v1/chat/completions HTTP/1.1\r\n'
                'Host: 127.0.0.1\r\nContent-Type: application/json\r\n'
                f'Content-Length: {len(body)}\r\nConnection: keep-alive\r\n\r\n'
            ).encode() + body
            client = socket.create_connection(('127.0.0.1', listen), 2)
            client.sendall(request)
            end = time.monotonic() + 8
            while time.monotonic() < end and not closed:
                time.sleep(0.05)
            client.close()
            self.assertTrue(closed, 'gateway left the silent upstream open')
            self.assertLess(closed[0], 8)
        finally:
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGTERM)
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait(timeout=3)
            if proc.stderr:
                proc.stderr.close()
            upstream.close()
            tmp.cleanup()


if __name__ == '__main__':
    unittest.main(verbosity=2)
