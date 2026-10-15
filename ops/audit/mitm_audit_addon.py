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


addons = [MagAudit()]
