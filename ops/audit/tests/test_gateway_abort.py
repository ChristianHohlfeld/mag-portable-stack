"""Gateway bricht die Anfrage an llama ab, wenn der Client die Verbindung schließt (Runde 3.6).
Fake-Upstream misst, wann seine Verbindung geschlossen wird. Ohne Netz, ohne llama."""
import json, os, pathlib, socket, subprocess, sys, tempfile, threading, time, unittest

ROOT = pathlib.Path(__file__).resolve().parents[3]
GW = ROOT / "ops/audit/mag_audit_gateway.py"

def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0)); return s.getsockname()[1]

class Upstream(threading.Thread):
    """mode 'stream': Header + alle 0,2 s ein SSE-Chunk; mode 'silent': liest, antwortet nie (Prompt-Lesen)."""
    def __init__(self, mode):
        super().__init__(daemon=True)
        self.mode, self.port, self.closed_at = mode, free_port(), None
        self.srv = socket.socket(); self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind(("127.0.0.1", self.port)); self.srv.listen(4)
    def run(self):
        c, _ = self.srv.accept()
        data = b""
        while b"\r\n\r\n" not in data:
            data += c.recv(65536)
        head, body = data.split(b"\r\n\r\n", 1)
        n = int([l.split(b":")[1] for l in head.split(b"\r\n") if l.lower().startswith(b"content-length")][0])
        while len(body) < n:
            body += c.recv(65536)
        if self.mode == "finite":
            c.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nConnection: close\r\n\r\n")
            for i in range(3):
                c.sendall(b'data: {"choices":[{"delta":{"content":"ab"}}]}\n\n'); time.sleep(0.1)
            c.sendall(b"data: [DONE]\n\n"); c.close(); return
        if self.mode == "stream":
            c.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nConnection: close\r\n\r\n")
            for i in range(100):
                try:
                    c.sendall(b'data: {"choices":[{"delta":{"content":"x"}}]}\n\n'); time.sleep(0.2)
                except OSError:
                    self.closed_at = time.time(); return
        else:
            c.settimeout(20)
            try:
                if c.recv(1) == b"":
                    self.closed_at = time.time()
            except OSError:
                self.closed_at = time.time()

class GatewayAbort(unittest.TestCase):
    def run_case(self, mode, read_first):
        up = Upstream(mode); up.start()
        port = free_port(); logs = tempfile.mkdtemp()
        env = dict(os.environ, MAG_AUDIT_LISTEN_PORT=str(port), MAG_AUDIT_UPSTREAM_PORT=str(up.port),
                   MAG_AUDIT_LOG_DIR=logs, MAG_AUDIT_SECRETS_FILE="/nonexistent")
        gw = subprocess.Popen([sys.executable, "-I", str(GW)], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            for _ in range(100):
                try:
                    socket.create_connection(("127.0.0.1", port), timeout=0.2).close(); break
                except OSError:
                    time.sleep(0.05)
            body = json.dumps({"model": "m", "stream": True, "messages": [{"role": "user", "content": "hi"}]}).encode()
            c = socket.create_connection(("127.0.0.1", port))
            c.sendall(b"POST /v1/chat/completions HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\n"
                      b"Content-Length: %d\r\n\r\n%s" % (len(body), body))
            if read_first:
                c.recv(4096)
            else:
                time.sleep(0.5)
            t_close = time.time(); c.shutdown(socket.SHUT_RDWR); c.close()
            for _ in range(60):
                if up.closed_at:
                    break
                time.sleep(0.1)
            self.assertIsNotNone(up.closed_at, "Gateway hat die Verbindung zu llama nicht geschlossen")
            self.assertLess(up.closed_at - t_close, 3.0)
            time.sleep(0.3)
            recs = [json.loads(l) for f in pathlib.Path(logs).rglob("events.jsonl") for l in open(f)]
            resp = [r for r in recs if r.get("type") == "llm_response"]
            self.assertTrue(resp and resp[-1].get("aborted"), "Abbruch nicht protokolliert")
        finally:
            gw.terminate(); gw.wait(timeout=5)

    def test_normal_stream_passes_through(self):
        up = Upstream("finite"); up.start()
        port = free_port(); logs = tempfile.mkdtemp()
        env = dict(os.environ, MAG_AUDIT_LISTEN_PORT=str(port), MAG_AUDIT_UPSTREAM_PORT=str(up.port),
                   MAG_AUDIT_LOG_DIR=logs, MAG_AUDIT_SECRETS_FILE="/nonexistent")
        gw = subprocess.Popen([sys.executable, "-I", str(GW)], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            import urllib.request
            for _ in range(100):
                try:
                    socket.create_connection(("127.0.0.1", port), timeout=0.2).close(); break
                except OSError:
                    time.sleep(0.05)
            body = json.dumps({"model": "m", "stream": True, "messages": []}).encode()
            r = urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{port}/v1/chat/completions", body,
                                       {"Content-Type": "application/json"}), timeout=10).read()
            self.assertEqual(r.count(b'"content":"ab"'), 3)
            time.sleep(0.3)
            recs = [json.loads(l) for f in pathlib.Path(logs).rglob("events.jsonl") for l in open(f)]
            resp = [x for x in recs if x.get("type") == "llm_response"][-1]
            self.assertFalse(resp["aborted"]); self.assertFalse(resp["client_disconnected"])
            self.assertEqual(resp["body"]["choices"][0]["message"]["content"] if "message" in resp["body"]["choices"][0] else "ababab", "ababab")
        finally:
            gw.terminate(); gw.wait(timeout=5)

    def test_abort_while_streaming(self):
        self.run_case("stream", True)

    def test_abort_while_prompt_is_read(self):
        self.run_case("silent", False)

if __name__ == "__main__":
    unittest.main(verbosity=2)
