#!/usr/bin/env python3
"""Tiny OpenAI-compatible stand-in for llama-server, for bootstrap tests only.

Installed by `ops/bootstrap.sh --test-stubs` as mag-llm-stub.service on
127.0.0.1:8090 (Q4 port) and :8091 (NUMA1 port) so the audit gateway, the
room's model status and /readyz/model work without 150 GB of weights.
Never installed on a production host (bootstrap requires the TEST-HOST marker).

  python3 ops/tests/stub-llm.py [PORT ...]
"""
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODEL = "deepseek-v4-flash-0731"
REPLY = "Stub-Antwort: der Bootstrap-Test laeuft ohne echtes Modell."


class Handler(BaseHTTPRequestHandler):
    server_version = "mag-stub-llm/1"

    def log_message(self, fmt, *args):  # keep journald quiet
        pass

    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/health":
            self._json(200, {"status": "ok"})
        elif path in ("/v1/models", "/v1/models/"):
            self._json(200, {"object": "list", "data": [
                {"id": MODEL, "object": "model", "owned_by": "mag-stub"}]})
        elif path == "/props":
            self._json(200, {"model_path": "/stub/" + MODEL + ".gguf",
                             "build_info": "stub", "total_slots": 1,
                             "default_generation_settings": {"n_ctx": 32768}})
        elif path == "/slots":
            self._json(200, [{"id": 0, "is_processing": False}])
        elif path == "/metrics":
            body = b"# stub\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self._json(404, {"error": {"message": "not found"}})

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            req = json.loads(raw or b"{}")
        except ValueError:
            req = {}
        path = self.path.split("?", 1)[0]
        if path.startswith("/slots/"):
            self._json(200, {"id_slot": 0, "n_saved": 0, "n_restored": 0})
            return
        if path not in ("/v1/chat/completions", "/chat/completions", "/v1/completions"):
            self._json(404, {"error": {"message": "not found"}})
            return
        created = int(time.time())
        usage = {"prompt_tokens": 8, "completion_tokens": 12, "total_tokens": 20}
        if req.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            for piece in (REPLY[:20], REPLY[20:]):
                chunk = {"id": "stub", "object": "chat.completion.chunk", "created": created,
                         "model": MODEL, "choices": [{"index": 0, "delta": {"content": piece},
                                                      "finish_reason": None}]}
                self.wfile.write(b"data: " + json.dumps(chunk).encode() + b"\n\n")
            done = {"id": "stub", "object": "chat.completion.chunk", "created": created,
                    "model": MODEL, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                    "usage": usage, "timings": {"predicted_per_second": 10.0}}
            self.wfile.write(b"data: " + json.dumps(done).encode() + b"\n\n")
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
            return
        self._json(200, {"id": "stub", "object": "chat.completion", "created": created,
                         "model": MODEL, "usage": usage,
                         "choices": [{"index": 0, "finish_reason": "stop",
                                      "message": {"role": "assistant", "content": REPLY}}]})


def main():
    ports = [int(p) for p in sys.argv[1:]] or [8090]
    servers = [ThreadingHTTPServer(("127.0.0.1", p), Handler) for p in ports]
    for s in servers[1:]:
        threading.Thread(target=s.serve_forever, daemon=True).start()
    servers[0].serve_forever()


if __name__ == "__main__":
    main()
