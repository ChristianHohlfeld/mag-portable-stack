#!/usr/bin/env python3
"""A scripted stand-in for llama-server: health, streaming chat completions
with tool calls, and the slot actions. It records every request so the room
tests can assert what the bots were actually sent. Like the real server it
refuses a request whose history holds a tool call with arguments that are no
JSON, and it can play a model that lost its way: raw arguments streamed piece
by piece, an error that quotes them. It proves the room's logic, not that a
real model or a real VM works."""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def text_of(content):
    """The text of a message, also when it carries images (a list of parts)."""
    if isinstance(content, list):
        return "".join(part.get("text", "") for part in content if part.get("type") == "text")
    return content or ""


class FakeLlama:
    def __init__(self, name):
        self.name = name
        self.requests = []      # chat request bodies, in order
        self.slot_actions = []  # ("erase"|"save"|"restore", filename)
        self.saved = set()
        self.delay = 0.0        # seconds between streamed pieces
        self.reply = None       # callable(body) -> str | dict, see _answer
        self.pieces = 0         # pieces of the last answer that went out
        self.hung_up = 0        # answers the room ended before they were complete
        self.refused = 0        # requests refused for unreadable tool-call arguments
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
                # The real server reads the arguments of every tool call in the
                # history and refuses the request when one is no JSON, quoting it.
                for message in body.get("messages", []):
                    for call in message.get("tool_calls") or []:
                        text = call["function"]["arguments"]
                        try:
                            readable = isinstance(json.loads(text), dict)
                        except ValueError:
                            readable = False
                        if not readable:
                            fake.refused += 1
                            return self._json(500, {"error": {"code": 500, "type": "server_error", "message": (
                                "Failed to parse tool call arguments as JSON: [json.exception.parse_error.101] parse error at line 1, column %d: "
                                "syntax error while parsing value - invalid string: missing closing quote; last read: '%s'"
                                % (len(text), text.replace("\n", "\\n")))}})
                answer = fake._answer(body)
                if isinstance(answer, dict) and answer.get("http_error"):
                    return self._json(500, {"error": {"code": 500, "type": "server_error", "message": answer["http_error"]}})
                fake.pieces = 0
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
                        fake.pieces += 1
                    calls = answer.get("tool_calls", []) if isinstance(answer, dict) else []
                    for index, call in enumerate(calls):
                        emit({"choices": [{"index": 0, "delta": {"tool_calls": [{
                            "index": index, "id": "call_%d_%d" % (len(fake.requests), index), "type": "function",
                            "function": {"name": call["name"], "arguments": json.dumps(call.get("arguments", {}))}}]}}]})
                    # Arguments as the server streams them while the model writes: text, piece by piece.
                    raw = answer.get("raw_tool_calls", []) if isinstance(answer, dict) else []
                    for index, call in enumerate(raw, len(calls)):
                        emit({"choices": [{"index": 0, "delta": {"tool_calls": [{
                            "index": index, "id": "call_%d_%d" % (len(fake.requests), index), "type": "function",
                            "function": {"name": call["name"], "arguments": ""}}]}}]})
                        for at in range(0, len(call["arguments"]), 40):
                            if fake.delay:
                                time.sleep(fake.delay)
                            emit({"choices": [{"index": 0, "delta": {"tool_calls": [{"index": index, "function": {"arguments": call["arguments"][at:at + 40]}}]}}]})
                            fake.pieces += 1
                    calls = calls + raw
                    emit({"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls" if calls else "stop"}],
                          "timings": {"prompt_n": 5, "cache_n": 100, "predicted_n": len(pieces), "predicted_per_second": 20.0}})
                    emit({"choices": [], "usage": {"prompt_tokens": 105, "completion_tokens": len(pieces)}})
                    emit("[DONE]")
                    self.wfile.write(b"0\r\n\r\n")
                    self.close_connection = True
                except (BrokenPipeError, ConnectionResetError):
                    fake.hung_up += 1  # the room ended the answer (Stop, or it had lost its way)

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
        if "Sonst antworte exakt PASS" in text_of(last.get("content")):
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
