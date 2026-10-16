"""
mitmproxy-Addon für mag-audit-webproxy: protokolliert jeden Web-/Browser-Zugriff der Bots
und Agent-VMs vollständig (URL, Methode, Status, Header, Request- und Response-Bodies,
WebSocket-Nachrichten) nach /var/log/mag-audit/YYYY/MM/DD/web.jsonl.

Bodies bis MAG_AUDIT_INLINE_MAX (Default 2 MiB) stehen als Text direkt im Datensatz;
größere oder binäre Bodies werden content-addressed unter /var/log/mag-audit/blobs/<sha256>
abgelegt (nichts geht verloren, Duplikate werden nur einmal gespeichert).

Start (siehe mag-audit-webproxy.service):
  mitmdump -s /opt/mag-stack/current/ops/audit/mitm_audit_addon.py ...
"""
import hashlib
import json
import os
import socket
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mag_audit_gateway import AuditWriter, REDACTOR, LOG_DIR, redact_headers  # noqa: E402

INLINE_MAX = int(os.environ.get("MAG_AUDIT_INLINE_MAX", str(2 * 1024 * 1024)))
BLOB_MAX = int(os.environ.get("MAG_AUDIT_BLOB_MAX", str(200 * 1024 * 1024)))
BLOB_DIR = os.path.join(LOG_DIR, "blobs")
# Zuordnung Client-IP oder Netz -> Agent-Name, z.B. "10.88.0.11=agent1,10.88.0.0/24=agent1,127.0.0.1=host"
import ipaddress  # noqa: E402
CLIENT_MAP, CLIENT_NETS = {}, []
for _kv in filter(None, os.environ.get("MAG_AUDIT_CLIENT_MAP", "").split(",")):
    _k, _v = _kv.split("=", 1)
    if "/" in _k:
        CLIENT_NETS.append((ipaddress.ip_network(_k.strip(), strict=False), _v.strip()))
    else:
        CLIENT_MAP[_k.strip()] = _v.strip()
CLIENT_NETS.sort(key=lambda n: -n[0].prefixlen)   # spezifischstes Netz zuerst

WRITER = AuditWriter(LOG_DIR, name="web.jsonl")

TEXTUAL = ("text/", "json", "javascript", "xml", "x-www-form-urlencoded", "csv", "graphql")


def _body(content, ctype):
    if content is None:
        return None
    raw = bytes(content)
    sha = hashlib.sha256(raw).hexdigest()
    info = {"size": len(raw), "sha256": sha, "content_type": ctype}
    is_text = any(t in (ctype or "").lower() for t in TEXTUAL)
    if is_text and len(raw) <= INLINE_MAX:
        info["text"] = REDACTOR.text(raw.decode("utf-8", "replace"))
        return info
    if len(raw) <= BLOB_MAX:
        os.makedirs(BLOB_DIR, mode=0o2750, exist_ok=True)
        path = os.path.join(BLOB_DIR, sha[:2], sha)
        if not os.path.exists(path):
            os.makedirs(os.path.dirname(path), mode=0o2750, exist_ok=True)
            data = raw
            if is_text:  # auch große Texte vor dem Ablegen von Secrets befreien
                data = REDACTOR.text(raw.decode("utf-8", "replace")).encode("utf-8")
            tmp = path + ".tmp"
            with open(tmp, "wb") as fh:
                fh.write(data)
            os.chmod(tmp, 0o640)
            os.replace(tmp, path)
        info["blob"] = os.path.relpath(path, LOG_DIR)
    else:
        info["truncated"] = True  # > BLOB_MAX: nur Hash + Größe
    return info


def _content(msg):
    """Dekodierten Body (gzip/br entpackt) liefern; bei Fehler den Rohinhalt."""
    try:
        return msg.get_content(strict=False)
    except Exception:
        return msg.raw_content


def _client(flow):
    ip = flow.client_conn.peername[0] if flow.client_conn.peername else None
    if ip and ip.startswith("::ffff:"):
        ip = ip[7:]
    agent = CLIENT_MAP.get(ip or "")
    if agent is None and ip:
        try:
            a = ipaddress.ip_address(ip)
            agent = next((name for net, name in CLIENT_NETS if a in net), None)
        except ValueError:
            pass
    return {"ip": ip, "agent": agent or "unknown"}


class MagAudit:
    def response(self, flow):
        self._log(flow)

    def error(self, flow):
        self._log(flow)

    def websocket_message(self, flow):
        msg = flow.websocket.messages[-1]
        WRITER.write({
            "type": "web_ws_message", "flow_id": flow.id, "client": _client(flow),
            "url": REDACTOR.text(flow.request.pretty_url),
            "from_client": msg.from_client,
            "body": _body(msg.content, "text/plain" if msg.is_text else "application/octet-stream"),
        })

    def _log(self, flow):
        req, resp = flow.request, flow.response
        rec = {
            "type": "web_request",
            "flow_id": flow.id,
            "client": _client(flow),
            "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(req.timestamp_start)),
            "duration_s": round((resp.timestamp_end if resp and resp.timestamp_end else time.time())
                                - req.timestamp_start, 3),
            "method": req.method,
            "url": REDACTOR.text(req.pretty_url),
            "http_version": req.http_version,
            "request": {"headers": redact_headers(dict(req.headers)),
                        "body": _body(_content(req), req.headers.get("content-type"))},
        }
        if resp is not None:
            rec["response"] = {"status": resp.status_code, "reason": resp.reason,
                               "headers": redact_headers(dict(resp.headers)),
                               "body": _body(_content(resp), resp.headers.get("content-type"))}
        if flow.error:
            rec["error"] = str(flow.error)
        WRITER.write(rec)


BROWSER_PORT = int(os.environ.get("MAG_AUDIT_BROWSER_PORT", "8889"))
BLOCKLIST_FILE = os.environ.get("MAG_AUDIT_BLOCKLIST", "/etc/mag-audit/blocklist.txt")


def _blocklist():
    try:
        with open(BLOCKLIST_FILE) as fh:
            return {l.strip().lower().lstrip(".") for l in fh if l.strip() and not l.startswith("#")}
    except OSError:
        return set()


def _blocked(host):
    host = (host or "").lower().rstrip(".")
    bl = _blocklist()
    return any(host == d or host.endswith("." + d) for d in bl)


def _browser_port(conn):
    try:
        return conn.sockname and conn.sockname[1] == BROWSER_PORT
    except Exception:
        return False


class BrowserPassthrough:
    """Port 8889 = Bot-Browser: HTTPS als CONNECT-Tunnel OHNE TLS-Aufbruch (echter Chrome-
    Handshake/JA4 zum Ziel). Protokolliert nur Host/Port/SNI; Domain-Blockliste gilt weiter.
    Port 8888 (Shell/Git/Tools) bleibt vollständig auditiert."""

    def http_connect(self, flow):
        if not _browser_port(flow.client_conn):
            return
        host, port = flow.request.host, flow.request.port
        if _blocked(host):
            from mitmproxy import http
            flow.response = http.Response.make(403, b"blocked by mag-audit blocklist\n")
        WRITER.write({"type": "browser_tunnel", "flow_id": flow.id, "client": _client(flow),
                      "host": host, "port": port, "blocked": _blocked(host),
                      "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})

    def server_connect(self, data):
        # Optional: Browser-Tunnel über IPv4 ausleiten (MAG_BROWSER_EGRESS=ipv4 in webproxy.env).
        # Test 16.10.: v4 (92.209.235.110) ebenso 429/sorry wie v6 -> Default aus.
        if os.environ.get("MAG_BROWSER_EGRESS", "any") != "ipv4":
            return
        if not _browser_port(data.client):
            return
        host, port = data.server.address
        try:
            import ipaddress
            if ipaddress.ip_address(host).version == 4:
                return
        except ValueError:
            pass
        try:
            v4 = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM)[0][4][0]
        except OSError:
            return  # kein A-Record: Standardverhalten
        data.server.address = (v4, port)
        if not data.server.sni:
            data.server.sni = host

    def tls_clienthello(self, data):
        if _browser_port(data.context.client):
            data.ignore_connection = True
            WRITER.write({"type": "browser_tls", "sni": data.client_hello.sni,
                          "client": {"ip": (data.context.client.peername or [None])[0]}})


addons = [BrowserPassthrough(), MagAudit()]
