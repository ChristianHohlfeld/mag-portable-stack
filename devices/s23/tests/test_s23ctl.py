#!/usr/bin/env python3
"""S23 control daemon: auth, loopback bind, one session, adb argv, latency."""
# mag-test-timeout: 180
import pathlib as _mag_path
import sys as _mag_sys
_mag_root = _mag_path.Path(__file__).resolve()
while _mag_root != _mag_root.parent and not (_mag_root / 'ops/ci/mag_deadline.py').is_file():
    _mag_root = _mag_root.parent
_mag_sys.path.insert(0, str(_mag_root / 'ops/ci'))
import mag_deadline as _mag_deadline
_mag_deadline.arm(180)
import base64
import http.client
import os
import socket
import subprocess
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BIN = ROOT / "build" / "s23ctl"
TOKEN = "test-token-value-1"


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def http_req(port, method, path, token=TOKEN, body=None, session=None, timeout=5):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    headers = {"Authorization": "Bearer " + token}
    if session:
        headers["X-S23-Session"] = session
    if body is not None:
        headers["Content-Type"] = "application/json"
    conn.request(method, path, body, headers)
    res = conn.getresponse()
    data = res.read()
    status, latency = res.status, res.getheader("X-S23-Latency-Ms")
    conn.close()
    return status, data, latency


class S23Ctl(unittest.TestCase):
    def test_bind_and_port_policy(self):
        self.assertEqual(subprocess.call([str(BIN), "--check-bind", "127.0.0.1"]), 0)
        self.assertEqual(subprocess.call([str(BIN), "--check-bind", "::1"]), 0)
        self.assertNotEqual(subprocess.call([str(BIN), "--check-bind", "0.0.0.0"]), 0)
        self.assertNotEqual(subprocess.call([str(BIN), "--check-bind", "8.8.8.8"]), 0)
        self.assertNotEqual(subprocess.call([str(BIN), "--check-bind", "100.64.1.1"]), 0)
        env = os.environ.copy()
        env["MAG_S23_BIND_TAILNET"] = "1"
        self.assertEqual(subprocess.call([str(BIN), "--check-bind", "100.64.1.1"], env=env), 0)
        self.assertEqual(subprocess.call([str(BIN), "--check-bind", "100.127.255.255"], env=env), 0)
        self.assertNotEqual(subprocess.call([str(BIN), "--check-bind", "100.128.0.1"], env=env), 0)
        self.assertNotEqual(subprocess.call([str(BIN), "--check-bind", "8.8.8.8"], env=env), 0)
        self.assertNotEqual(subprocess.call([str(BIN), "--check-port", "8720"]), 0)
        self.assertEqual(subprocess.call([str(BIN), "--check-port", "18721"]), 0)

    def test_refuses_public_bind_s23pub_port_and_missing_token(self):
        base = os.environ.copy()
        base.update({"MAG_S23_TOKEN": TOKEN, "MAG_S23_SERIAL": "R5C", "MAG_S23_ADB": "/bin/true"})
        for extra in ({"MAG_S23_BIND": "0.0.0.0"}, {"MAG_S23_PORT": "8720"}, {"MAG_S23_TOKEN": "short"}):
            env = base.copy()
            env.update(extra)
            proc = subprocess.run([str(BIN)], env=env, capture_output=True, text=True, timeout=3)
            self.assertNotEqual(proc.returncode, 0, extra)
            self.assertNotIn(TOKEN, proc.stderr)

    def test_control_session_and_latency(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            log = tmp / "adb.log"
            fail = tmp / "fail-once"
            slow = tmp / "slow"
            script = tmp / "adb"
            script.write_text(textwrap.dedent("""\
                #!/bin/bash
                printf '%s\\n' "$*" >> "$MAG_S23_FAKE_LOG"
                if [[ "$*" == *reconnect* ]]; then exit 0; fi
                if [[ -f "$MAG_S23_FAKE_FAIL" ]]; then
                  rm -f "$MAG_S23_FAKE_FAIL"
                  echo 'error: device offline' >&2
                  exit 1
                fi
                if [[ -f "$MAG_S23_FAKE_SLOW" ]]; then sleep 0.35; fi
                case "$*" in
                  *screencap*) printf 'PNGDATA'; exit 0 ;;
                  *get-state*) echo device; exit 0 ;;
                  *wm\\ size*) echo 'Physical size: 1080x2340'; exit 0 ;;
                  *echo\\ s23*) echo s23; exit 0 ;;
                  *) exit 0 ;;
                esac
                """))
            script.chmod(0o755)
            port = free_port()
            env = os.environ.copy()
            env.update({
                "MAG_S23_TOKEN": TOKEN,
                "MAG_S23_ADB": str(script),
                "MAG_S23_SERIAL": "R5CTEST",
                "MAG_S23_PORT": str(port),
                "MAG_S23_BIND": "127.0.0.1",
                "MAG_S23_FAKE_LOG": str(log),
                "MAG_S23_FAKE_FAIL": str(fail),
                "MAG_S23_FAKE_SLOW": str(slow),
                "MAG_S23_CMD_TIMEOUT_MS": "2000",
                "MAG_S23_SESSION_IDLE_MS": "5000",
                "MAG_S23_FRAME_MS": "80",
            })
            fail.write_text("1")
            proc = subprocess.Popen([str(BIN)], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                self._wait(port)
                status, _, _ = http_req(port, "GET", "/health", token="nope")
                self.assertEqual(status, 401)
                status, body, _ = http_req(port, "GET", "/health")
                self.assertEqual(status, 200)
                self.assertIn(b'"ok":true', body)
                self.assertIn(b"127.0.0.1", body)
                status, body, latency = http_req(port, "GET", "/v1/status")
                self.assertEqual(status, 200, body)
                self.assertIn(b"device", body)
                self.assertIn(b"1080x2340", body)
                self.assertTrue(latency and int(latency) >= 0)
                self.assertIn("reconnect", log.read_text())
                status, _, _ = http_req(port, "POST", "/v1/input/tap", body='{"x":1,"y":2}')
                self.assertEqual(status, 440)
                status, opened, _ = http_req(port, "POST", "/v1/session", body="{}")
                self.assertEqual(status, 200, opened)
                sid = opened.split(b'"session":"')[1].split(b'"')[0].decode()
                status, _, _ = http_req(port, "POST", "/v1/session", body="{}")
                self.assertEqual(status, 409)
                status, _, latency = http_req(port, "POST", "/v1/input/tap", body='{"x":10,"y":20}', session=sid)
                self.assertEqual(status, 200, latency)
                self.assertIn("-s R5CTEST shell input tap 10 20", log.read_text())
                status, _, _ = http_req(port, "POST", "/v1/input/swipe",
                                        body='{"x1":1,"y1":2,"x2":3,"y2":4,"ms":90}', session=sid)
                self.assertEqual(status, 200)
                self.assertIn("input swipe 1 2 3 4 90", log.read_text())
                status, _, _ = http_req(port, "POST", "/v1/input/text", body='{"text":"a;rm"}', session=sid)
                self.assertEqual(status, 400)
                self.assertNotIn("input text a;rm", log.read_text())
                status, _, _ = http_req(port, "POST", "/v1/input/text", body='{"text":"Ha llo"}', session=sid)
                self.assertEqual(status, 200)
                self.assertIn("input text Ha%sllo", log.read_text())
                status, _, _ = http_req(port, "POST", "/v1/input/key", body='{"key":"KEYCODE_HOME"}', session=sid)
                self.assertEqual(status, 200)
                self.assertIn("input keyevent KEYCODE_HOME", log.read_text())
                slow.write_text("1")
                status, body, latency = http_req(port, "GET", "/v1/latency", session=sid)
                self.assertEqual(status, 200, body)
                self.assertGreaterEqual(int(latency), 200)
                slow.unlink()
                status, png, latency = http_req(port, "GET", "/v1/screenshot", session=sid)
                self.assertEqual(status, 200)
                self.assertEqual(png, b"PNGDATA")
                self.assertTrue(latency)
                self._video(port, sid)
                status, _, _ = http_req(port, "DELETE", "/v1/session", session=sid)
                self.assertEqual(status, 200)
                self.assertNotIn(TOKEN, log.read_text())
            finally:
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()

    def _wait(self, port):
        for _ in range(50):
            try:
                status, _, _ = http_req(port, "GET", "/health")
                if status == 200:
                    return
            except (ConnectionError, OSError):
                time.sleep(0.05)
        self.fail("s23ctl did not accept")

    def _video(self, port, session):
        sock = socket.create_connection(("127.0.0.1", port), timeout=3)
        key = base64.b64encode(b"0123456789abcdef").decode()
        req = (
            "GET /v1/video HTTP/1.1\r\n"
            "Host: 127.0.0.1\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            f"Authorization: Bearer {TOKEN}\r\n"
            f"X-S23-Session: {session}\r\n\r\n"
        )
        sock.sendall(req.encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            buf += sock.recv(4096)
        head, rest = buf.split(b"\r\n\r\n", 1)
        self.assertIn(b"101", head.split(b"\r\n", 1)[0])

        def frame(buf):
            while len(buf) < 2:
                buf += sock.recv(4096)
            opcode = buf[0] & 0x0F
            ln = buf[1] & 0x7F
            pos = 2
            if ln == 126:
                while len(buf) < 4:
                    buf += sock.recv(4096)
                ln = int.from_bytes(buf[2:4], "big")
                pos = 4
            elif ln == 127:
                while len(buf) < 10:
                    buf += sock.recv(4096)
                ln = int.from_bytes(buf[2:10], "big")
                pos = 10
            while len(buf) < pos + ln:
                buf += sock.recv(4096)
            return opcode, buf[pos:pos + ln], buf[pos + ln:]

        op, payload, rest = frame(rest)
        self.assertEqual(op, 1)
        self.assertIn(b"latency_ms", payload)
        op, payload, rest = frame(rest)
        self.assertEqual(op, 2)
        self.assertEqual(payload, b"PNGDATA")
        sock.sendall(bytes([0x88, 0x80, 0, 0, 0, 0]))
        sock.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
