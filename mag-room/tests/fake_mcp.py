#!/usr/bin/env python3
"""Stand-in for a remote MCP server (Streamable HTTP): initialize with a
session id, tools/list, tools/call. Answers as JSON or as an event stream,
wants a bearer token when one is set, and records what it was asked."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOOLS = [
    {"name": "lookup", "title": "Nachschlagen", "description": "Looks a note up by its key.", "annotations": {"readOnlyHint": True},
     "inputSchema": {"type": "object", "properties": {"key": {"type": "string"}}, "required": ["key"]}},
    {"name": "write_note", "description": "Writes a note.",
     "inputSchema": {"type": "object", "properties": {"key": {"type": "string"}, "text": {"type": "string"}}, "required": ["key", "text"]}},
    {"name": "boom", "description": "Always fails.", "annotations": {"readOnlyHint": True}, "inputSchema": {"type": "object", "properties": {}}},
]


class FakeMcp:
    def __init__(self, token=""):
        self.token, self.tools, self.notes = token, list(TOOLS), {"farbe": "blau"}
        self.calls, self.sessions, self.inits, self.refused = [], [], 0, 0
        self.stream = False   # True: answers come as text/event-stream
        self.expire = False   # True: the next call finds its session gone (once)
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def answer(self, message, session=None):
                if fake.stream:
                    body = ("event: message\ndata: " + json.dumps({"jsonrpc": "2.0", "method": "notifications/progress", "params": {}}) + "\n\n" +
                            "event: message\ndata: " + json.dumps(message) + "\n\n").encode()
                else:
                    body = json.dumps(message).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream" if fake.stream else "application/json")
                if session:
                    self.send_header("Mcp-Session-Id", session)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def plain(self, code, text=""):
                body = text.encode()
                self.send_response(code)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                message = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
                if fake.token and self.headers.get("Authorization") != "Bearer " + fake.token:
                    fake.refused += 1
                    return self.plain(401, json.dumps({"error": "unauthorized"}))
                method, session = message.get("method"), self.headers.get("Mcp-Session-Id")

                def result(value, **more):
                    self.answer({"jsonrpc": "2.0", "id": message["id"], "result": value}, **more)
                if method == "initialize":
                    fake.inits += 1
                    fake.sessions.append("session-%d" % fake.inits)
                    return result({"protocolVersion": message["params"]["protocolVersion"], "capabilities": {"tools": {}},
                                   "serverInfo": {"name": "fake-notes", "version": "1.2"}, "instructions": "Notes live here. Look a note up before you write it."},
                                  session=fake.sessions[-1])
                if method == "notifications/initialized":
                    return self.plain(202)
                if session not in fake.sessions:
                    return self.plain(404, "no such session")
                if method == "tools/list":
                    return result({"tools": fake.tools})
                if method == "tools/call":
                    if fake.expire:
                        fake.expire = False
                        fake.sessions.remove(session)
                        return self.plain(404, "session expired")
                    name, args = message["params"]["name"], message["params"].get("arguments") or {}
                    fake.calls.append({"tool": name, "args": args, "session": session, "protocol": self.headers.get("MCP-Protocol-Version")})
                    if name == "lookup":
                        return result({"content": [{"type": "text", "text": fake.notes.get(args.get("key"), "(nichts)")}]})
                    if name == "write_note":
                        fake.notes[args["key"]] = args["text"]
                        return result({"content": [{"type": "text", "text": "gespeichert"}], "structuredContent": {"ok": True}})
                    if name == "boom":
                        return result({"content": [{"type": "text", "text": "das ging schief"}], "isError": True})
                    return self.answer({"jsonrpc": "2.0", "id": message["id"], "error": {"code": -32602, "message": "Unknown tool: " + name}})
                self.answer({"jsonrpc": "2.0", "id": message.get("id"), "error": {"code": -32601, "message": "Method not found"}})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        self.url = "http://127.0.0.1:%d/mcp" % self.port
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
