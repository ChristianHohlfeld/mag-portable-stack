#!/usr/bin/env python3
"""Merge a tailscaled ServeConfig so only Gitea is published with Funnel.

Port 443 is a raw TCP Funnel with PROXY protocol v2 to nginx 127.0.0.1:4443.
tailscaled must not terminate TLS on that port: HTTPS mode only knows
mag.tail3e02ae.ts.net and answers other SNI with 'no webserver configured
for name/port' (tlsv1 alert internal error). nginx SNI-selects the room,
bots, pages and Gitea. Public https://mag.tail3e02ae.ts.net/ is the same
TCP path (Funnel relay does not decrypt). Room, bots and pages then
allow only tailnet PROXY clients (100.64.0.0/10) so a public Host/SNI
switch cannot read those vhosts. HTTP vhosts must not bind
100.123.114.16:443. Tailnet serves on 8443, 8445 and 8446 stay HTTPS
proxies and are not funneled.
"""
import json
import pathlib
import re
import sys

NODE = "mag.tail3e02ae.ts.net"
FUNNEL_PORT = 443
# Raw TLS + PROXY v2. nginx on this socket terminates TLS and picks SNI.
FUNNEL_TCP = "127.0.0.1:4443"
FUNNEL_PROXY_PROTOCOL = 2
# Gitea behind the shared nginx vhost (not tailscaled HTTP).
FUNNEL_PROXY = "http://127.0.0.1:3000"
FUNNEL_ORIGIN = "https://mag.tail3e02ae.ts.net"
# Shared by every tailnet vhost. A second listen on this socket with different
# flags is an nginx emerg. HTTP must not bind 100.123.114.16:443; tailscaled
# TCP-forwards that address here.
NGINX_LISTEN = "listen 127.0.0.1:4443 ssl http2 proxy_protocol;"
# Live mag-room.conf since 2026-10-24 uses this exact line. Bots/pages may
# omit http2. Any listen on the Tailscale address :443 is the collision.
OLD_NGINX_LISTEN = "listen 100.123.114.16:443 ssl http2;"
# Live bots/pages still use this after @TAILNET_BIND@ substitution. Any
# listen on the Tailscale address :443 is the Funnel collision, including
# variants without http2 and leftover template placeholders.
# HTTP/ssl leftover only. A stream `listen 100.123.114.16:443;` has no ssl
# and must not be rewritten onto 4443.
OLD_NGINX_LISTEN_RE = re.compile(
    r"^([ \t]*)listen[ \t]+(?:100\.123\.114\.16|@TAILNET_BIND@):443\b[^;]*\bssl[^;]*;",
    re.M,
)
NGINX_T_FILE_RE = re.compile(r"^# configuration file (.+):$")
TAILNET_443_NEEDLE = re.compile(
    r"listen[ \t]+(?:100\.123\.114\.16|@TAILNET_BIND@):443\b[^;]*\bssl\b"
)
# Existing tailnet-only serves. 8443 is already in use. The room stays on 8446.
TAILNET = {
    "8443": "http://127.0.0.1:18767",
    "8445": "http://127.0.0.1:3000",
    "8446": "http://127.0.0.1:8766",
}


def hostport(port):
    return f"{NODE}:{port}"


def funnel_key():
    return hostport(FUNNEL_PORT)


def _proxy(value):
    if not isinstance(value, str) or "://" not in value:
        raise SystemExit("serve proxy must be an absolute URL")
    return value.rstrip("/")


def retarget_nginx(text):
    """Move an installed tailnet vhost off the Tailscale address."""
    if not isinstance(text, str):
        raise SystemExit("nginx config must be text")
    return OLD_NGINX_LISTEN_RE.sub(r"\1" + NGINX_LISTEN, text)


def has_tailnet_443(text):
    """True when a listen still claims the Tailscale address on :443."""
    if not isinstance(text, str):
        return False
    return bool(TAILNET_443_NEEDLE.search(text))


def files_from_nginx_t(text):
    """Paths nginx -T reported that still listen on the Tailscale :443."""
    if not isinstance(text, str):
        raise SystemExit("nginx -T dump must be text")
    current = None
    found = []
    seen = set()
    for line in text.splitlines():
        marker = NGINX_T_FILE_RE.match(line)
        if marker:
            current = marker.group(1).strip()
            continue
        if current and has_tailnet_443(line) and current not in seen:
            seen.add(current)
            found.append(current)
    return found


def files_from_trees(roots):
    """Regular files and symlink targets under roots that still bind :443."""
    found = []
    seen = set()
    suffixes = {".conf", ".inc", ".in"}
    for root in roots:
        path = pathlib.Path(root)
        if path.is_file() or path.is_symlink():
            candidates = [path]
        elif path.is_dir():
            candidates = sorted(path.rglob("*"))
        else:
            continue
        for item in candidates:
            if item.suffix not in suffixes and item.name != "nginx.conf":
                continue
            try:
                text = item.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            key = str(item)
            if has_tailnet_443(text) and key not in seen:
                seen.add(key)
                found.append(key)
    return found


def rewrite_path(path):
    """Follow a symlink when the target is still a managed nginx file.

    sites-enabled/mag-room.conf -> sites-available/mag-room.conf. A symlink
    out of /etc/nginx (a release tree) stays, so apply can replace that
    path with a regular file and leave the release immutable.
    """
    p = pathlib.Path(path)
    try:
        if not p.is_symlink():
            return str(p)
        real = p.resolve()
        if not real.is_file():
            return str(p)
        real_s = str(real)
        if real_s.startswith("/etc/nginx/") or real_s.startswith("/etc/mag-ci/"):
            return real_s
        if not str(p).startswith("/etc/"):
            return real_s
    except OSError:
        pass
    return str(p)


def rewrite_destinations(path):
    """Include path plus managed symlink target.

    nginx -T reports sites-enabled/mag-room.conf. Following only the
    symlink used to drop that include path, so a leftover regular file
    (or a stale include that no longer matches sites-available) stayed
    on 100.123.114.16:443 after bots/pages were moved.
    """
    p = str(pathlib.Path(path))
    dests = [p]
    followed = rewrite_path(path)
    if followed not in dests:
        dests.append(followed)
    return dests


def candidate_files(nginx_t="", roots=()):
    """Union of nginx -T hits and on-disk leftover listens."""
    found = []
    seen = set()
    for path in files_from_nginx_t(nginx_t) + files_from_trees(roots):
        for dest in rewrite_destinations(path):
            if dest not in seen:
                seen.add(dest)
                found.append(dest)
    return found


def _https_proxy(tcp, web, port, proxy, only=False):
    handler = tcp.get(port) or {}
    if not isinstance(handler, dict):
        handler = {}
    for stale in ("TCPForward", "TerminateTLS", "HTTP", "ProxyProtocol"):
        handler.pop(stale, None)
    handler["HTTPS"] = True
    tcp[port] = handler
    hp = hostport(port)
    server = web.get(hp) or {}
    if not isinstance(server, dict):
        server = {}
    if only:
        handlers = {}
    else:
        handlers = server.get("Handlers") or {}
        if not isinstance(handlers, dict):
            handlers = {}
    handlers["/"] = {"Proxy": proxy}
    server["Handlers"] = handlers
    web[hp] = server


def load_cfg(path):
    raw = pathlib.Path(path).read_text().strip()
    if not raw:
        return {}
    data = json.loads(raw)
    return {} if data is None else data


def serve_kind(block):
    """https, tcp, or empty. tailscaled 1.104.1 rejects https↔tcp on one port."""
    if not isinstance(block, dict):
        return ""
    if block.get("HTTPS") is True or block.get("HTTP") is True:
        return "https"
    if block.get("TCPForward"):
        return "tcp"
    return ""


def conflict_message(current, wanted):
    """Match validateServeConfigUpdate: want tcp but port already serving https."""
    if not isinstance(current, dict):
        current = {}
    if not isinstance(wanted, dict):
        wanted = {}
    current_tcp = current.get("TCP") or {}
    wanted_tcp = wanted.get("TCP") or {}
    if not isinstance(current_tcp, dict):
        current_tcp = {}
    if not isinstance(wanted_tcp, dict):
        wanted_tcp = {}
    ports = {str(p) for p in current_tcp} | {str(p) for p in wanted_tcp}
    for port in sorted(ports, key=lambda item: (len(item), item)):
        have = serve_kind(current_tcp.get(port))
        want = serve_kind(wanted_tcp.get(port))
        if have and want and have != want:
            return f"want to serve {want}, but port {port} is already serving {have}"
    return None


def transition(current):
    """Drop :443 so a later POST can change HTTPS↔TCP without a 500."""
    if current is None:
        current = {}
    if not isinstance(current, dict):
        raise SystemExit("serve config must be a JSON object")
    cfg = json.loads(json.dumps(current))
    tcp = cfg.get("TCP")
    if isinstance(tcp, dict):
        tcp.pop(str(FUNNEL_PORT), None)
        if tcp:
            cfg["TCP"] = tcp
        else:
            cfg.pop("TCP", None)
    web = cfg.get("Web")
    if isinstance(web, dict):
        web.pop(funnel_key(), None)
        if web:
            cfg["Web"] = web
        else:
            cfg.pop("Web", None)
    allow = cfg.get("AllowFunnel")
    if isinstance(allow, dict):
        allow.pop(funnel_key(), None)
        if allow:
            cfg["AllowFunnel"] = allow
        else:
            cfg.pop("AllowFunnel", None)
    return cfg


def merge(current):
    if current is None:
        current = {}
    if not isinstance(current, dict):
        raise SystemExit("serve config must be a JSON object")
    cfg = json.loads(json.dumps(current))
    tcp = cfg.get("TCP") or {}
    web = cfg.get("Web") or {}
    if not isinstance(tcp, dict) or not isinstance(web, dict):
        raise SystemExit("serve TCP/Web must be objects")
    for port, proxy in TAILNET.items():
        _https_proxy(tcp, web, port, proxy)
    # Public Funnel is TCP passthrough. HTTPS-on-443 made tailscaled terminate
    # TLS and reject every SNI except mag.tail3e02ae.ts.net.
    port = str(FUNNEL_PORT)
    tcp[port] = {
        "TCPForward": FUNNEL_TCP,
        "ProxyProtocol": FUNNEL_PROXY_PROTOCOL,
    }
    web.pop(funnel_key(), None)
    cfg["TCP"] = tcp
    cfg["Web"] = web
    cfg["AllowFunnel"] = {funnel_key(): True}
    return cfg


def assert_public_gitea_only(cfg):
    if not isinstance(cfg, dict):
        raise SystemExit("serve config must be a JSON object")
    allow = cfg.get("AllowFunnel") or {}
    if allow != {funnel_key(): True}:
        raise SystemExit("funnel allowlist must be Gitea on 443 only")
    web = cfg.get("Web") or {}
    tcp = cfg.get("TCP") or {}
    block = tcp.get(str(FUNNEL_PORT)) or {}
    if block.get("TCPForward") != FUNNEL_TCP:
        raise SystemExit("funnel port is not TCP-forwarded to nginx 4443")
    if block.get("ProxyProtocol") != FUNNEL_PROXY_PROTOCOL:
        raise SystemExit("funnel port is missing PROXY protocol v2")
    if block.get("HTTPS") or block.get("HTTP") or block.get("TerminateTLS"):
        raise SystemExit("funnel port must not terminate TLS (that swallows tailnet SNI)")
    if funnel_key() in web:
        raise SystemExit("funnel port must not have a Web handler")
    for port, proxy in TAILNET.items():
        if _proxy(web[hostport(port)]["Handlers"]["/"]["Proxy"]) != proxy:
            raise SystemExit(f"tailnet serve :{port} changed")
        if tcp.get(port, {}).get("HTTPS") is not True:
            raise SystemExit(f"tailnet port {port} is not HTTPS")
        if tcp.get(port, {}).get("TCPForward"):
            raise SystemExit(f"tailnet port {port} is a raw forward")
        if hostport(port) in allow:
            raise SystemExit(f"tailnet port {port} is funneled")
    for key, on in allow.items():
        if on and key != funnel_key():
            raise SystemExit("a non-Gitea endpoint is funneled")


def main(argv):
    mode = argv[1] if len(argv) > 1 else "merge"
    if mode == "needs-transition":
        if len(argv) < 4:
            raise SystemExit(
                "usage: merge-gitea-funnel.py needs-transition CURRENT WANTED"
            )
        msg = conflict_message(load_cfg(argv[2]), load_cfg(argv[3]))
        if msg:
            sys.stdout.write(msg + "\n")
            return 0
        return 1
    raw = sys.stdin.read()
    if mode == "retarget":
        sys.stdout.write(retarget_nginx(raw))
        return 0
    if mode == "candidates":
        for path in candidate_files(raw, argv[2:]):
            sys.stdout.write(path + "\n")
        return 0
    if mode == "scan":
        leftover = files_from_nginx_t(raw)
        if leftover:
            sys.stderr.write(
                "gitea funnel: nginx still listens on 100.123.114.16:443 in:\n"
            )
            for path in leftover:
                sys.stderr.write(f"  {path}\n")
            return 1
        return 0
    current = json.loads(raw) if raw.strip() else {}
    if mode == "transition":
        cfg = transition(current if current is not None else {})
        json.dump(cfg, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
        return 0
    if mode == "merge":
        merged = merge(current)
        assert_public_gitea_only(merged)
        json.dump(merged, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
        return 0
    if mode == "check":
        assert_public_gitea_only(current if current is not None else {})
        return 0
    if mode == "origin":
        sys.stdout.write(FUNNEL_ORIGIN + "\n")
        return 0
    raise SystemExit(
        "usage: merge-gitea-funnel.py merge|check|origin|retarget|candidates|scan|transition|needs-transition"
    )


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv))
    except KeyError as exc:
        raise SystemExit(f"serve config missing {exc}") from None
