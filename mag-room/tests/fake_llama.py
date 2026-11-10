#!/usr/bin/env python3
"""A scripted stand-in for llama-server: health, streaming chat completions
with tool calls, and the slot actions. It records every request so the room
tests can assert what the bots were actually sent. It proves the room's
logic, not that a real model or a real VM works."""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class FakeLlama:
    def __init__(self, name):
        self.name = name
        self.requests = []      # chat request bodies, in order
        self.slot_actions = []  # ("erase"|"save"|"restore", filename)
        self.saved = set()
        self.delay = 0.0        # seconds between streamed pieces
        self.reply = None       # callable(body) -> str | dict, see _answer
        self.lock = threading.Lock()
        fake = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def _json(self, code, value):
                body = json.dumps(value).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path == "/health":
                    return self._json(200, {"status": "ok"})
                if self.path == "/props":
                    return self._json(200, {"model_alias": fake.name})
                self._json(404, {"error": "not found"})

            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(length) if length else b""
                if self.path.startswith("/slots/0"):
                    action = self.path.split("action=")[-1]
                    filename = (json.loads(raw or b"{}") or {}).get("filename", "")
                    with fake.lock:
                        fake.slot_actions.append((action, filename))
                        if action == "save":
                            fake.saved.add(filename)
                        known = filename in fake.saved
                    if action == "restore" and not known:
                        return self._json(400, {"error": {"message": "invalid slot save file"}})
                    return self._json(200, {"id_slot": 0, "action": action})
                if self.path != "/v1/chat/completions":
                    return self._json(404, {"error": "not found"})
                body = json.loads(raw)
                with fake.lock:
                    fake.requests.append(body)
                answer = fake._answer(body)
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()

                def emit(obj):
                    data = ("data: " + (obj if isinstance(obj, str) else json.dumps(obj)) + "\n\n").encode()
                    self.wfile.write(b"%x\r\n%s\r\n" % (len(data), data))
                    self.wfile.flush()

                try:
                    emit({"choices": [{"index": 0, "delta": {"role": "assistant", "content": None}}]})
                    if isinstance(answer, dict) and answer.get("reasoning"):
                        emit({"choices": [{"index": 0, "delta": {"reasoning_content": answer["reasoning"]}}]})
                    text = answer if isinstance(answer, str) else answer.get("content", "")
                    limit = body.get("max_tokens", 4096)
                    pieces = [text[i:i + 6] for i in range(0, len(text), 6)][:limit]
                    for piece in pieces:
                        if fake.delay:
                            time.sleep(fake.delay)
                        emit({"choices": [{"index": 0, "delta": {"content": piece}}]})
                    calls = answer.get("tool_calls", []) if isinstance(answer, dict) else []
                    for index, call in enumerate(calls):
                        emit({"choices": [{"index": 0, "delta": {"tool_calls": [{
                            "index": index, "id": "call_%d_%d" % (len(fake.requests), index), "type": "function",
                            "function": {"name": call["name"], "arguments": json.dumps(call.get("arguments", {}))}}]}}]})
                    emit({"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls" if calls else "stop"}],
                          "timings": {"prompt_n": 5, "cache_n": 100, "predicted_n": len(pieces), "predicted_per_second": 20.0}})
                    emit({"choices": [], "usage": {"prompt_tokens": 105, "completion_tokens": len(pieces)}})
                    emit("[DONE]")
                    self.wfile.write(b"0\r\n\r\n")
                    self.close_connection = True
                except (BrokenPipeError, ConnectionResetError):
                    pass  # the room cancelled the turn

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    # What the "model" says. Tests override `reply`; by default it answers
    # human messages and passes on bot-triggered turns, like a polite bot.
    def _answer(self, body):
        if self.reply:
            return self.reply(body)
        last = body["messages"][-1]
        if last["role"] == "tool":
            return "Erledigt."
        if "Sonst antworte exakt PASS" in (last.get("content") or ""):
            return "PASS"
        return "Hallo von %s." % self.name

    def last_user(self):
        with self.lock:
            for message in reversed(self.requests[-1]["messages"]):
                if message["role"] == "user":
                    return message["content"]
        return ""

    def chats(self):
        """Chat requests without the one-token prefix warm-ups."""
        with self.lock:
            return [r for r in self.requests if r.get("max_tokens") != 1]

    def close(self):
        self.server.shutdown()
        self.server.server_close()
