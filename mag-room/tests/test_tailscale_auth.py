"""Real C++ magroom -> fake tailscaled Unix LocalAPI; no Python runtime service.

The fake socket is a test fixture only. Verify both Content-Length and Go-style
chunked HTTP, strict identity parsing and fail-closed behavior without tailscaled.
"""
import http.server
import json
import os
import pathlib
import socket
import socketserver
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[2]


def unused_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class TailnetLocalAPI(socketserver.StreamRequestHandler):
    mode = "ok"
    seen = []

    def handle(self):
        first = self.rfile.readline().decode("ascii", errors="replace")
        while self.rfile.readline() not in (b"\r\n", b""):
            pass
        type(self).seen.append(first)
        if type(self).mode == "down":
            self.wfile.write(b"HTTP/1.1 503 Unavailable\r\nContent-Length: 0\r\n\r\n")
            return
        login = "ada@github" if type(self).mode != "bad-login" else "bad login"
        payload = json.dumps({"UserProfile": {
            "LoginName": login, "DisplayName": "\u00c5da L\u00f6velace"
        }}).encode()
        if type(self).mode == "chunked":
            chunk = ("%X\r\n" % len(payload)).encode() + payload + b"\r\n0\r\n\r\n"
            response = b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n" + chunk
        elif type(self).mode == "missing":
            response = b'HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}'
        else:
            response = b"HTTP/1.1 200 OK\r\nContent-Length: " + str(len(payload)).encode() + b"\r\n\r\n" + payload
        self.wfile.write(response)


class UnixServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


class NativeWhois(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="mag-native-whois-")
        cls.path = pathlib.Path(cls.tmp.name)
        cls.sock = cls.path / "tailscaled.sock"
        cls.unix = UnixServer(str(cls.sock), TailnetLocalAPI)
        threading.Thread(target=cls.unix.serve_forever, daemon=True).start()
        cls.port = unused_port()
        cls.bridge_port = unused_port()
        state = cls.path / "room"
        state.mkdir()
        (state / "agents.json").write_text("[]")
        env = dict(os.environ, MAG_ROOM_BIND="127.0.0.1", MAG_ROOM_PORT=str(cls.port),
                   MAG_ROOM_BRIDGE_IP="127.0.0.1", MAG_ROOM_BRIDGE_PORT=str(cls.bridge_port),
                   MAG_ROOM_STATE=str(state), MAG_ROOM_STATIC=str(ROOT / "mag-room/static"),
                   MAG_ROOM_UI=str(ROOT / "mag-room/web/index.html"),
                   MAG_NETMODE="/bin/true", MAG_TEST_NO_MEMGIT="1",
                   MAG_ROOM_FILE_SYNC_SECONDS="0",
                   MAG_ROOM_MODEL_STATUS=str(cls.path / "model.json"),
                   MAG_TAILSCALED_SOCKET=str(cls.sock))
        cls.log = open(cls.path / "server.log", "w")
        cls.proc = subprocess.Popen([str(ROOT / "build/magroom")], env=env,
                                    stdout=cls.log, stderr=cls.log)
        for _ in range(100):
            if cls.proc.poll() is not None:
                raise RuntimeError((cls.path / "server.log").read_text())
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{cls.port}/healthz", timeout=1
                ) as response:
                    if response.status == 200:
                        break
            except Exception:
                time.sleep(.05)
        else:
            raise RuntimeError("magroom did not become ready")

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        try:
            cls.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            cls.proc.kill()
            cls.proc.wait()
        cls.log.close()
        cls.unix.shutdown()
        cls.unix.server_close()
        cls.tmp.cleanup()

    def request(self, remote=None, method="GET"):
        headers = {"X-Mag-Remote": remote} if remote is not None else {}
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/_internal/tailscale/whois",
            headers=headers, method=method)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(req, timeout=5) as response:
                return response.status, response.headers
        except urllib.error.HTTPError as error:
            return error.code, error.headers

    def test_valid_whois_headers_and_real_client_addr(self):
        TailnetLocalAPI.mode = "ok"
        code, headers = self.request("100.101.102.103:54321")
        self.assertEqual(code, 200)
        self.assertEqual(headers["X-Tailscale-User"], "ada@github")
        self.assertEqual(headers["X-Tailscale-Login"], "ada@github")
        self.assertEqual(headers["X-Tailscale-Name"], "%C3%85da%20L%C3%B6velace")
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertIn("addr=100.101.102.103%3A54321", TailnetLocalAPI.seen[-1])

    def test_chunked_localapi_response(self):
        TailnetLocalAPI.mode = "chunked"
        code, headers = self.request("100.101.102.103:54321")
        self.assertEqual(code, 200)
        self.assertEqual(headers["X-Tailscale-User"], "ada@github")

    def test_missing_or_malformed_remote_is_denied(self):
        TailnetLocalAPI.mode = "ok"
        for addr in (None, "", "8.8.8.8", "999.1.1.1:5000", "100.101.102.103:0",
                     "100.101.102.103:65536", "100.101.102.103:443\r\nX-Foo: x"):
            if addr and "\r" in addr:
                continue  # urllib refuses invalid header bytes before magroom sees them
            code, _ = self.request(addr)
            self.assertEqual(code, 403, addr)

    def test_invalid_login_and_localapi_failure_fail_closed(self):
        for mode in ("bad-login", "missing", "down"):
            TailnetLocalAPI.mode = mode
            code, headers = self.request("100.101.102.103:54321")
            self.assertEqual(code, 403, mode)
            self.assertNotIn("X-Tailscale-User", headers)

    def test_only_get_or_head(self):
        self.assertEqual(self.request("100.101.102.103:54321", "POST")[0], 405)


if __name__ == "__main__":
    unittest.main(verbosity=2)
