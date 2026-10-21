#!/usr/bin/env python3
"""Network tests for the standalone C++20 verifier (no real LLM required)."""
import http.server
import json
import pathlib
import subprocess
import tempfile
import threading
import unittest

ROOT = pathlib.Path(__file__).resolve().parent

class ModelFixture(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args): pass
    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.calls.append(payload)
        ids = [100, 103, 77, 119]
        if self.server.mode == "diverge" and len(self.server.calls) == 4:
            ids[2] += 1
        text = "different" if self.server.mode == "textdiff" and len(self.server.calls) == 4 else "same"
        obj = {"choices": [{"message": {"role": "assistant", "content": text}}]}
        if self.server.mode != "missingtokens": obj["token_ids"] = ids
        data = json.dumps(obj).encode()
        self.send_response(200)
        if self.server.mode == "chunked":
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            for piece in (data[:11], data[11:]):
                self.wfile.write(("%x\r\n" % len(piece)).encode() + piece + b"\r\n")
            self.wfile.write(b"0\r\n\r\n")
        else:
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

class EngineHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="mag-det-tests-")
        cls.binary = pathlib.Path(cls.tmp.name) / "mag_det"
        subprocess.run(["g++", "-std=c++20", "-O3", "-Wall", "-Wextra", "-pthread",
                        str(ROOT / "mag_deterministic_engine.cpp"), "-o", str(cls.binary)],
                       check=True)
    @classmethod
    def tearDownClass(cls): cls.tmp.cleanup()
    def setUp(self):
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), ModelFixture)
        self.server.mode = "normal"
        self.server.calls = []
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
    def cli(self, *args):
        return subprocess.run([str(self.binary), *args, "--host", "127.0.0.1",
            "--port", str(self.server.server_port), "--model", "qwen2.5-72b",
            "--numa", "none"], text=True, capture_output=True, timeout=45,
            cwd=self.tmp.name)
    def test_synthetic_selftest(self):
        r = self.cli("--selftest")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("[MAG-SELFTEST] PASS", r.stdout)
    def test_twenty_real_http_requests_stateless(self):
        r = self.cli("--verify")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(self.server.calls), 20)
        prompts = {x["messages"][0]["content"] for x in self.server.calls}
        self.assertEqual(len(prompts), 1)
        for x in self.server.calls:
            self.assertEqual(len(x["messages"]), 1)
            self.assertEqual(x["messages"][0]["role"], "user")
            self.assertEqual((x["temperature"], x["top_k"], x["seed"], x["cache_prompt"]),
                             (0, 1, 42, False))
            self.assertNotIn("tools", x)
    def test_infer_verifies_exact_input_20_plus_one(self):
        r = self.cli("--infer", "hi")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(self.server.calls), 21)
        self.assertEqual({x["messages"][0]["content"] for x in self.server.calls}, {"hi"})
        self.assertIn("21. Inferenzlauf", r.stdout)
    def test_diverging_token_fails_on_run_four(self):
        self.server.mode = "diverge"
        r = self.cli("--verify")
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(len(self.server.calls), 4)
        self.assertIn("Token-Abweichung", r.stderr)
    def test_different_text_same_tokens_fails(self):
        self.server.mode = "textdiff"
        r = self.cli("--verify")
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(len(self.server.calls), 4)
        self.assertIn("abweichender Antworttext", r.stderr)
    def test_no_real_token_ids_is_not_certified(self):
        self.server.mode = "missingtokens"
        r = self.cli("--verify")
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(len(self.server.calls), 1)
        self.assertIn("Keine echten Token-IDs", r.stderr)
    def test_chunked_http(self):
        self.server.mode = "chunked"
        r = self.cli("--verify")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(self.server.calls), 20)

if __name__ == "__main__":
    unittest.main(verbosity=2)
