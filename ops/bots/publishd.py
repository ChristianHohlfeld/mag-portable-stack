#!/usr/bin/env python3
"""Publish one bot site from its VM.

Listens on the libvirt gateway only. A guest POSTs a ustar archive of
/home/bot/www with the token written into that guest at spawn. The wildcard
certificate and DNS record are never touched.
"""
import hmac
import io
import os
import re
import shutil
import tarfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.environ.get("MAG_BOTS_ROOT", "/var/lib/mag-bots")
BIND = os.environ.get("MAG_BOTS_PUBLISH_BIND", "192.168.122.1")
PORT = int(os.environ.get("MAG_BOTS_PUBLISH_PORT", "8091"))
MAX_BODY = 8 * 1024 * 1024
MAX_FILE = 2 * 1024 * 1024
MAX_FILES = 200
ID_RE = re.compile(r"^agent[0-9]+$")
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,180}$")


def peer_ok(ip):
    if os.environ.get("MAG_BOTS_TESTING") == "1":
        return ip == "127.0.0.1"
    parts = ip.split(".")
    return len(parts) == 4 and parts[:3] == ["192", "168", "122"]


def token_for(bot_id):
    path = os.path.join(ROOT, "tokens", bot_id)
    if os.path.islink(path) or not os.path.isfile(path):
        return ""
    with open(path, "r", encoding="ascii") as handle:
        return handle.read().strip()


def members_of(payload):
    try:
        archive = tarfile.open(fileobj=io.BytesIO(payload), mode="r:")
    except tarfile.TarError as exc:
        raise ValueError("not a ustar archive") from exc
    found = []
    for member in archive.getmembers():
        if member.isdir():
            continue
        if not member.isreg():
            raise ValueError("unsupported archive member")
        name = member.name[2:] if member.name.startswith("./") else member.name
        if not name or name.startswith("/") or "\\" in name:
            raise ValueError("unsafe path")
        parts = name.split("/")
        if any(part in ("", ".", "..") for part in parts):
            raise ValueError("unsafe path")
        if not NAME_RE.match(name):
            raise ValueError("unsafe name")
        if member.size > MAX_FILE:
            raise ValueError("file too large")
        extracted = archive.extractfile(member)
        if extracted is None:
            raise ValueError("missing member")
        data = extracted.read()
        if len(data) != member.size:
            raise ValueError("short member")
        found.append((name, data))
        if len(found) > MAX_FILES:
            raise ValueError("too many files")
    if not any(name == "index.html" for name, _ in found):
        raise ValueError("index.html required")
    return found


def install_site(bot_id, files):
    www = os.path.join(ROOT, "www")
    os.makedirs(www, 0o755, exist_ok=True)
    if os.path.islink(www):
        raise ValueError("unsafe www")
    final = os.path.join(www, bot_id)
    staging = os.path.join(www, ".staging-" + bot_id)
    if os.path.islink(final) or os.path.islink(staging):
        raise ValueError("refusing symlink")
    shutil.rmtree(staging, ignore_errors=True)
    os.makedirs(staging, 0o755)
    try:
        staging_real = os.path.realpath(staging)
        for name, data in files:
            path = os.path.realpath(os.path.join(staging, name))
            if os.path.commonpath([staging_real, path]) != staging_real:
                raise ValueError("path escaped staging")
            os.makedirs(os.path.dirname(path), 0o755, exist_ok=True)
            with open(path, "wb") as handle:
                handle.write(data)
            os.chmod(path, 0o644)
        backup = os.path.join(www, ".old-" + bot_id)
        if os.path.islink(backup):
            raise ValueError("refusing symlink")
        shutil.rmtree(backup, ignore_errors=True)
        if os.path.isdir(final):
            os.rename(final, backup)
        os.rename(staging, final)
        shutil.rmtree(backup, ignore_errors=True)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        return

    def _send(self, code, body):
        data = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        if not peer_ok(self.client_address[0]):
            self._send(403, "forbidden\n")
            return
        if self.path.split("?", 1)[0] != "/publish":
            self._send(404, "not found\n")
            return
        bot_id = self.headers.get("X-Mag-Bot", "")
        if not ID_RE.match(bot_id):
            self._send(400, "bad bot\n")
            return
        auth = self.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            self._send(403, "forbidden\n")
            return
        presented = auth[7:].strip()
        expected = token_for(bot_id)
        if not expected or not hmac.compare_digest(presented, expected):
            self._send(403, "forbidden\n")
            return
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            self._send(411, "length required\n")
            return
        if length < 0 or length > MAX_BODY:
            self._send(413, "too large\n")
            return
        payload = self.rfile.read(length)
        if len(payload) != length:
            self._send(400, "short body\n")
            return
        try:
            install_site(bot_id, members_of(payload))
        except ValueError as exc:
            self._send(400, str(exc) + "\n")
            return
        self._send(204, "")


def main():
    os.umask(0o022)
    httpd = ThreadingHTTPServer((BIND, PORT), Handler)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
