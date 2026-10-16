#!/usr/bin/env python3
"""
mag-audit-gateway – zentrale, protokollierende Schnittstelle zwischen allen Bots
auf mag und dem LLM-Backend (llama serve).

  Bots/Clients  --->  127.0.0.1:8080 (dieses Gateway)  --->  127.0.0.1:8090 (llama serve)

Was protokolliert wird (JSONL, ein Datensatz pro Zeile, append-only, Hash-Kette):
  * llm_request   – kompletter Request (messages inkl. Tool-Ergebnisse, tools, Parameter)
  * llm_response  – komplette Antwort: content, reasoning_content, tool_calls, finish_reason,
                    usage/timings. Streaming (SSE) wird transparent durchgereicht und
                    parallel zu einer vollständigen Antwort zusammengesetzt.
  * bot_event     – frei definierte Ereignisse der Bots (Befehlsausführung, Browser-Aktionen,
                    Tool-Ergebnisse …) per POST /audit/v1/event
  * gateway       – Start/Stop/Fehler

Secrets: Auth-/Cookie-Header werden immer maskiert; zusätzlich werden alle exakten Werte aus
MAG_AUDIT_SECRETS_FILE (Format: name=wert, eine Zeile pro Secret) überall durch
"[SECRET:name]" ersetzt, bevor irgendetwas auf die Platte geht.

Nur Python-Standardbibliothek, keine Abhängigkeiten.
"""
import hashlib
import http.client
import http.server
import json
import os
import re
import select
import signal
import socket
import socketserver
import sys
import threading
import time
import uuid
from datetime import datetime, timezone

LISTEN_HOST = os.environ.get("MAG_AUDIT_LISTEN_HOST", "127.0.0.1")
LISTEN_PORT = int(os.environ.get("MAG_AUDIT_LISTEN_PORT", "8080"))
UPSTREAM_HOST = os.environ.get("MAG_AUDIT_UPSTREAM_HOST", "127.0.0.1")
UPSTREAM_PORT = int(os.environ.get("MAG_AUDIT_UPSTREAM_PORT", "8090"))
UPSTREAM_TIMEOUT = max(1800.0, float(os.environ.get("MAG_AUDIT_UPSTREAM_TIMEOUT", "7200")))  # >= 30 min
LOG_DIR = os.environ.get("MAG_AUDIT_LOG_DIR", "/var/log/mag-audit")
SECRETS_FILE = os.environ.get("MAG_AUDIT_SECRETS_FILE", "/etc/mag-audit/secrets.list")
KEEP_RAW_SSE = os.environ.get("MAG_AUDIT_KEEP_RAW_SSE", "0") == "1"
MAX_EVENT_BYTES = int(os.environ.get("MAG_AUDIT_MAX_EVENT_BYTES", str(64 * 1024 * 1024)))

REDACT_HEADERS = {"authorization", "proxy-authorization", "cookie", "set-cookie",
                  "x-api-key", "api-key", "x-auth-token", "x-subscription-token",
                  "x-goog-api-key", "openai-organization"}
HOP_BY_HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
              "te", "trailers", "transfer-encoding", "upgrade", "content-length"}


# --------------------------------------------------------------------------- secrets
class Redactor:
    def __init__(self, path):
        self.path = path
        self.mtime = None
        self.pairs = []  # (wert, name), längste zuerst
        self.lock = threading.Lock()
        self.reload()

    def reload(self):
        try:
            st = os.stat(self.path)
        except FileNotFoundError:
            self.pairs, self.mtime = [], None
            return
        if st.st_mtime == self.mtime:
            return
        pairs = []
        with open(self.path, encoding="utf-8") as fh:
            for line in fh:
                line = line.rstrip("\n")
                if not line or line.lstrip().startswith("#") or "=" not in line:
                    continue
                name, value = line.split("=", 1)
                if len(value) >= 6:  # sehr kurze Werte würden zu viel maskieren
                    pairs.append((value, name.strip()))
        pairs.sort(key=lambda p: len(p[0]), reverse=True)
        with self.lock:
            self.pairs, self.mtime = pairs, st.st_mtime

    def text(self, s):
        if not s or not self.pairs:
            return s
        for value, name in self.pairs:
            if value in s:
                s = s.replace(value, f"[SECRET:{name}]")
        return s

    def obj(self, o):
        if isinstance(o, str):
            return self.text(o)
        if isinstance(o, list):
            return [self.obj(x) for x in o]
        if isinstance(o, dict):
            return {self.text(k) if isinstance(k, str) else k: self.obj(v) for k, v in o.items()}
        return o


REDACTOR = Redactor(SECRETS_FILE)


def redact_headers(headers):
    out = {}
    for k, v in headers.items():
        out[k] = "[REDACTED]" if k.lower() in REDACT_HEADERS else REDACTOR.text(v)
    return out


# --------------------------------------------------------------------------- writer
class AuditWriter:
    """Tagesdateien /var/log/mag-audit/YYYY/MM/DD/<name> mit SHA-256-Hash-Kette (eine Kette pro Datei/Prozess)."""

    def __init__(self, base, name="events.jsonl"):
        self.base = base
        self.name = name
        self.lock = threading.Lock()
        self.cur_path = None
        self.fh = None
        self.prev = None

    def _open_for(self, now):
        path = os.path.join(self.base, now.strftime("%Y"), now.strftime("%m"),
                            now.strftime("%d"), self.name)
        if path == self.cur_path:
            return
        if self.fh:
            self.fh.close()
        os.makedirs(os.path.dirname(path), mode=0o2750, exist_ok=True)
        prev = None
        if os.path.exists(path):
            prev = self._last_hash(path)
        if prev is None:
            prev = self._previous_day_hash(now)
        self.fh = open(path, "a", encoding="utf-8")
        os.chmod(path, 0o640)
        self.cur_path, self.prev = path, prev or ("0" * 64)

    @staticmethod
    def _last_hash(path):
        try:
            with open(path, "rb") as fh:
                fh.seek(0, 2)
                size = fh.tell()
                fh.seek(max(0, size - 1024 * 1024))
                lines = fh.read().splitlines()
            for line in reversed(lines):
                try:
                    return json.loads(line)["hash"]
                except Exception:
                    continue
        except FileNotFoundError:
            pass
        return None

    def _previous_day_hash(self, now):
        # Kette über Tagesgrenzen: letzten Hash der jüngsten älteren Datei suchen.
        best = None
        for root, _dirs, files in os.walk(self.base):
            if self.name in files:
                p = os.path.join(root, self.name)
                if best is None or p > best:
                    best = p
        return self._last_hash(best) if best else None

    def write(self, record):
        now = datetime.now(timezone.utc)
        record = REDACTOR.obj(record)
        with self.lock:
            self._open_for(now)
            record["ts"] = now.isoformat(timespec="milliseconds")
            record["prev_hash"] = self.prev
            body = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            h = hashlib.sha256((self.prev + body).encode("utf-8")).hexdigest()
            line = body[:-1] + f',"hash":"{h}"}}\n'
            self.fh.write(line)
            self.fh.flush()
            os.fsync(self.fh.fileno())
            self.prev = h


WRITER = AuditWriter(LOG_DIR)


# --------------------------------------------------------------------------- client attribution
def peer_uid(client_addr, local_port):
    """Ermittelt die UID des lokalen Prozesses hinter einer TCP-Verbindung über /proc/net/tcp."""
    ip, port = client_addr[0], client_addr[1]
    if not (ip.startswith("127.") or ip in ("::1", "::ffff:127.0.0.1")):
        return None
    cport, sport = f"{port:04X}", f"{local_port:04X}"
    for fn in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            with open(fn) as fh:
                next(fh)
                for line in fh:
                    parts = line.split()
                    loc_ip, loc_port = parts[1].rsplit(":", 1)
                    _rem_ip, rem_port = parts[2].rsplit(":", 1)
                    # Socket des Clients: lokaler Port = Client-Port, entfernter Port = Gateway-Port
                    if loc_port == cport and rem_port == sport:
                        return int(parts[7])
        except (FileNotFoundError, StopIteration):
            continue
    return None


def uid_name(uid):
    if uid is None:
        return None
    try:
        import pwd
        return pwd.getpwuid(uid).pw_name
    except KeyError:
        return str(uid)


# --------------------------------------------------------------------------- SSE assembly
class StreamAssembler:
    def __init__(self):
        self.buf = b""
        self.raw = [] if KEEP_RAW_SSE else None
        self.choices = {}
        self.meta = {}
        self.usage = None
        self.timings = None
        self.errors = []

    def feed(self, chunk):
        if self.raw is not None:
            self.raw.append(chunk.decode("utf-8", "replace"))
        self.buf += chunk
        while b"\n" in self.buf:
            line, self.buf = self.buf.split(b"\n", 1)
            self._line(line.strip())

    def _line(self, line):
        if not line.startswith(b"data:"):
            return
        data = line[5:].strip()
        if data == b"[DONE]" or not data:
            return
        try:
            ev = json.loads(data)
        except ValueError:
            self.errors.append(data.decode("utf-8", "replace")[:2000])
            return
        for k in ("id", "model", "created", "system_fingerprint"):
            if k in ev and k not in self.meta:
                self.meta[k] = ev[k]
        if ev.get("usage"):
            self.usage = ev["usage"]
        if ev.get("timings"):
            self.timings = ev["timings"]
        if "error" in ev:
            self.errors.append(ev["error"])
        for ch in ev.get("choices", []) or []:
            idx = ch.get("index", 0)
            st = self.choices.setdefault(idx, {"content": "", "reasoning_content": "",
                                               "tool_calls": {}, "finish_reason": None,
                                               "role": None})
            d = ch.get("delta") or {}
            if d.get("role"):
                st["role"] = d["role"]
            if d.get("content"):
                st["content"] += d["content"]
            for rk in ("reasoning_content", "reasoning"):
                if d.get(rk):
                    st["reasoning_content"] += d[rk]
            for tc in d.get("tool_calls") or []:
                ti = tc.get("index", 0)
                t = st["tool_calls"].setdefault(ti, {"id": None, "type": "function",
                                                     "function": {"name": "", "arguments": ""}})
                if tc.get("id"):
                    t["id"] = tc["id"]
                if tc.get("type"):
                    t["type"] = tc["type"]
                fn = tc.get("function") or {}
                if fn.get("name"):
                    t["function"]["name"] += fn["name"]
                if fn.get("arguments"):
                    t["function"]["arguments"] += fn["arguments"]
            if ch.get("finish_reason"):
                st["finish_reason"] = ch["finish_reason"]

    def result(self):
        if self.buf.strip():
            self._line(self.buf.strip())
        choices = []
        for idx in sorted(self.choices):
            st = self.choices[idx]
            calls = [st["tool_calls"][i] for i in sorted(st["tool_calls"])]
            for c in calls:  # Argumente zusätzlich geparst ablegen, wenn gültiges JSON
                try:
                    c["function"]["arguments_parsed"] = json.loads(c["function"]["arguments"])
                except Exception:
                    pass
            choices.append({"index": idx, "finish_reason": st["finish_reason"],
                            "message": {"role": st["role"] or "assistant",
                                        "content": st["content"],
                                        "reasoning_content": st["reasoning_content"],
                                        "tool_calls": calls}})
        out = dict(self.meta)
        out["choices"] = choices
        if self.usage is not None:
            out["usage"] = self.usage
        if self.timings is not None:
            out["timings"] = self.timings
        if self.errors:
            out["stream_errors"] = self.errors
        if self.raw is not None:
            out["raw_sse"] = "".join(self.raw)
        return out


# --------------------------------------------------------------------------- HTTP handler
class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "mag-audit-gateway/1.0"

    def log_message(self, fmt, *args):  # kein Request-Logging ins Journal (Secrets!)
        pass

    def _caller(self):
        uid = peer_uid(self.client_address, self.server.server_address[1])
        return {"peer": f"{self.client_address[0]}:{self.client_address[1]}",
                "uid": uid, "user": uid_name(uid),
                "bot": self.headers.get("X-Mag-Bot"),
                "session": self.headers.get("X-Mag-Session")}

    def _read_body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_EVENT_BYTES:
            raise ValueError("body too large")
        return self.rfile.read(n) if n else b""

    def _send_json(self, code, obj):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    # ---- eigene Endpunkte
    def _audit_endpoint(self):
        if self.path == "/audit/v1/health":
            return self._send_json(200, {"ok": True, "log_dir": LOG_DIR})
        if self.path == "/audit/v1/event" and self.command == "POST":
            body = self._read_body()
            try:
                ev = json.loads(body or b"{}")
            except ValueError:
                return self._send_json(400, {"error": "invalid json"})
            rid = str(uuid.uuid4())
            WRITER.write({"type": "bot_event", "id": rid, "caller": self._caller(),
                          "event": ev})
            return self._send_json(200, {"ok": True, "id": rid})
        return self._send_json(404, {"error": "unknown audit endpoint"})

    def _watch_client(self, up_sock, done, flag):
        """Bricht der Client ab (Raum: 'Alle pausieren'), wird die Verbindung zu llama sofort
        geschlossen. llama erkennt das und bricht die Anfrage ab, statt sie zu Ende zu rechnen
        und die nächste dahinter warten zu lassen. Prüft alle 0,5 s, ob der Client die Verbindung
        geschlossen hat (FIN/RST); ohne Lesen von Nutzdaten."""
        sock = self.connection
        while not done.is_set():
            try:
                r, _, _ = select.select([sock], [], [], 0.5)
                if not r:
                    continue
                if sock.recv(1, socket.MSG_PEEK | socket.MSG_DONTWAIT) != b"":
                    done.wait(0.5)      # Daten statt Ende (z.B. Pipelining): weiter beobachten
                    continue
            except (BlockingIOError, InterruptedError):
                continue
            except OSError:
                pass                    # Reset/Fehler = Client weg
            flag.set()
            try:
                up_sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            return

    def _proxy(self):
        if self.path.startswith("/audit/"):
            return self._audit_endpoint()
        REDACTOR.reload()
        rid = str(uuid.uuid4())
        t0 = time.time()
        caller = self._caller()
        body = self._read_body()
        try:
            req_json = json.loads(body) if body else None
        except ValueError:
            req_json = None
        is_llm = self.path.startswith(("/v1/", "/completion", "/chat", "/infill", "/apply-template"))
        quiet = self.path in ("/health", "/v1/models", "/props", "/metrics", "/slots")
        if not quiet:
            WRITER.write({"type": "llm_request" if is_llm else "http_request", "id": rid,
                          "caller": caller, "method": self.command, "path": self.path,
                          "headers": redact_headers(dict(self.headers)),
                          "body": req_json if req_json is not None
                          else body.decode("utf-8", "replace")})

        fwd_headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP_BY_HOP
                       and k.lower() != "host" and not k.lower().startswith("x-mag-")}
        fwd_headers["Host"] = f"{UPSTREAM_HOST}:{UPSTREAM_PORT}"
        fwd_headers["X-Mag-Audit-Id"] = rid
        conn = http.client.HTTPConnection(UPSTREAM_HOST, UPSTREAM_PORT, timeout=UPSTREAM_TIMEOUT)
        done, aborted = threading.Event(), threading.Event()
        try:
            conn.connect()
            up_sock = conn.sock   # http.client gibt conn.sock später an die Antwort ab (dann None)
            if is_llm and not quiet:
                threading.Thread(target=self._watch_client, args=(up_sock, done, aborted), daemon=True).start()
            conn.request(self.command, self.path, body=body or None, headers=fwd_headers)
            resp = conn.getresponse()
        except Exception as e:
            done.set()
            if aborted.is_set():
                WRITER.write({"type": "llm_response", "id": rid, "caller": caller, "path": self.path,
                              "status": None, "client_disconnected": True, "aborted": True,
                              "duration_s": round(time.time() - t0, 3), "body": None})
                conn.close()
                self.close_connection = True
                return
            WRITER.write({"type": "gateway_error", "id": rid, "caller": caller,
                          "path": self.path, "error": repr(e)})
            conn.close()
            return self._send_json(502, {"error": "upstream unavailable", "audit_id": rid})

        ctype = resp.getheader("Content-Type", "")
        streaming = "text/event-stream" in ctype
        self.send_response(resp.status, resp.reason)
        for k, v in resp.getheaders():
            if k.lower() not in HOP_BY_HOP:
                self.send_header(k, v)
        self.send_header("X-Mag-Audit-Id", rid)

        client_gone = False
        if streaming:
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            asm = StreamAssembler()
            while True:
                try:
                    chunk = resp.read1(65536)
                except (OSError, http.client.HTTPException):
                    if aborted.is_set():
                        break           # Client hat abgebrochen, Verbindung zu llama ist geschlossen
                    raise
                if not chunk:
                    break
                asm.feed(chunk)
                if not client_gone:
                    try:
                        self.wfile.write(b"%x\r\n%s\r\n" % (len(chunk), chunk))
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError):
                        # Client weg: llama sofort stoppen (Verbindung schließen); geloggt wird,
                        # was bis dahin kam
                        client_gone = True
                        aborted.set()
                        try:
                            up_sock.shutdown(socket.SHUT_RDWR)
                        except OSError:
                            pass
                        break
            if not client_gone:
                try:
                    self.wfile.write(b"0\r\n\r\n")
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    client_gone = True
            resp_record = asm.result()
        else:
            try:
                data = resp.read()
            except (OSError, http.client.HTTPException):
                if not aborted.is_set():
                    raise
                data = b""
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                client_gone = True
            try:
                resp_record = json.loads(data) if data else None
            except ValueError:
                resp_record = data.decode("utf-8", "replace")
        done.set()
        conn.close()
        client_gone = client_gone or aborted.is_set()

        if not quiet:
            WRITER.write({"type": "llm_response" if is_llm else "http_response", "id": rid,
                          "caller": caller, "path": self.path, "status": resp.status,
                          "streamed": streaming, "client_disconnected": client_gone,
                          "aborted": aborted.is_set(),
                          "duration_s": round(time.time() - t0, 3), "body": resp_record})
        if client_gone:
            self.close_connection = True

    do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = _proxy


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main():
    srv = Server((LISTEN_HOST, LISTEN_PORT), Handler)
    WRITER.write({"type": "gateway", "event": "start", "pid": os.getpid(),
                  "listen": f"{LISTEN_HOST}:{LISTEN_PORT}",
                  "upstream": f"{UPSTREAM_HOST}:{UPSTREAM_PORT}"})

    def stop(*_):
        WRITER.write({"type": "gateway", "event": "stop", "pid": os.getpid()})
        threading.Thread(target=srv.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    print(f"mag-audit-gateway listening on {LISTEN_HOST}:{LISTEN_PORT} -> "
          f"{UPSTREAM_HOST}:{UPSTREAM_PORT}, log dir {LOG_DIR}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    sys.exit(main())
