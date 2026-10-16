#!/usr/bin/env python3
"""Local whois proxy for Caddy forward_auth.

Caddy (stock binary, no tailscale module) strips client identity headers, then
calls this process on 127.0.0.1. We ask tailscaled's LocalAPI who is on the
TCP connection and return X-Tailscale-User / X-Tailscale-Name / X-Tailscale-Login.
Anything that is not a positive whois answer is a non-2xx response so Caddy
fails closed.

The display name is percent-encoded (UTF-8) so the header stays ASCII.
Bind address is loopback only. No credentials are read or stored.
"""
import json
import os
import re
import socket
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import quote

SOCKET = os.environ.get("TAILSCALED_SOCKET", "/var/run/tailscale/tailscaled.sock")
HOST = "127.0.0.1"
PORT = int(os.environ.get("MAG_WHOIS_PORT", "8765"))
LOGIN_RE = re.compile(r"^[A-Za-z0-9._@+-]{1,160}$")
REMOTE_RE = re.compile(r"^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3}):(\d{1,5})$")


def valid_remote(value):
    m = REMOTE_RE.fullmatch((value or "").strip())
    if not m:
        return ""
    if any(int(m.group(i)) > 255 for i in range(1, 5)):
        return ""
    if int(m.group(5)) > 65535:
        return ""
    return "%d.%d.%d.%d:%d" % tuple(int(m.group(i)) for i in range(1, 6))


def query_localapi(addr):
    """Return the parsed LocalAPI whois JSON for ip:port."""
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.settimeout(2.0)
        sock.connect(SOCKET)
        path = "/localapi/v0/whois?addr=" + quote(addr, safe=".:")
        req = (
            "GET %s HTTP/1.1\r\nHost: local-tailscaled.sock\r\n"
            "Connection: close\r\n\r\n" % path
        )
        sock.sendall(req.encode("ascii"))
        data = b""
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            data += chunk
            if len(data) > 1024 * 1024:
                raise RuntimeError("whois response too large")
    finally:
        sock.close()
    head, _, body = data.partition(b"\r\n\r\n")
    first = head.split(b"\r\n", 1)[0]
    parts = first.split()
    if len(parts) < 2 or parts[1] != b"200":
        raise RuntimeError("whois HTTP %s" % (parts[1].decode("ascii", "replace") if len(parts) > 1 else "?"))
    return json.loads(body.decode("utf-8"))


def parse_whois(obj):
    profile = obj.get("UserProfile") or {}
    login = str(profile.get("LoginName") or "").strip()
    name = str(profile.get("DisplayName") or "").strip() or login
    if not LOGIN_RE.fullmatch(login):
        raise ValueError("whois login missing or not a Tailscale login")
    name = "".join(ch for ch in name if ch >= " " and ch not in "[]\r\n")[:80] or login.split("@", 1)[0]
    return login, name


def header_name(name):
    return quote(name, safe="")


class Handler(BaseHTTPRequestHandler):
    query = staticmethod(query_localapi)
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        sys.stderr.write("mag-whois " + (fmt % args) + "\n")

    def _send(self, code, headers=None, body=b""):
        self.send_response(code)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in headers or []:
            self.send_header(k, v)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_GET(self):
        if self.path.split("?", 1)[0] not in ("/whois", "/auth"):
            self._send(404, body=b"not found\n")
            return
        remote = valid_remote(self.headers.get("X-Mag-Remote", ""))
        if not remote:
            self._send(400, body=b"missing remote\n")
            return
        try:
            login, name = parse_whois(self.query(remote))
        except Exception as exc:
            self.log_message("deny %s (%s)", remote, exc)
            self._send(401, body=b"whois denied\n")
            return
        self._send(
            200,
            [
                ("X-Tailscale-User", login),
                ("X-Tailscale-Login", login),
                ("X-Tailscale-Name", header_name(name)),
            ],
        )

    def do_HEAD(self):
        self.do_GET()


def main():
    ThreadingHTTPServer.address_family = socket.AF_INET
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    sys.stderr.write("mag-whois listening on %s:%d socket %s\n" % (HOST, PORT, SOCKET))
    httpd.serve_forever()


if __name__ == "__main__":
    main()
