#!/usr/bin/env python3
"""Merge a tailscaled ServeConfig so only Gitea is published with Funnel.

Port 443 is an HTTPS Funnel. tailscaled terminates TLS and proxies to Gitea
on http://127.0.0.1:3000. nginx must not bind the Tailscale address on :443.
Tailnet serves on 8443, 8445 and 8446 stay HTTPS proxies and are not funneled.
"""
import json
import pathlib
import re
import sys

NODE = "mag.tail3e02ae.ts.net"
FUNNEL_PORT = 443
# tailscaled is the HTTPS endpoint. It proxies HTTP to Gitea. Only 127.0.0.1
# is a valid Funnel backend.
FUNNEL_PROXY = "http://127.0.0.1:3000"
FUNNEL_ORIGIN = "https://mag.tail3e02ae.ts.net"
# Shared by every tailnet vhost. A second listen on this socket with different
# flags is an nginx emerg. The Tailscale address itself is not in the line:
# tailscaled is the only process bound to 100.123.114.16:443.
NGINX_LISTEN = "listen 127.0.0.1:4443 ssl http2 proxy_protocol;"
# Live mag-room.conf since 2026-10-24 uses this exact line. Bots/pages may
# omit http2. Any listen on the Tailscale address :443 is the collision.
OLD_NGINX_LISTEN = "listen 100.123.114.16:443 ssl http2;"
# Live bots/pages still use this after @TAILNET_BIND@ substitution. Any
# listen on the Tailscale address :443 is the Funnel collision, including
# variants without http2 and leftover template placeholders.
OLD_NGINX_LISTEN_RE = re.compile(
    r"^([ \t]*)listen[ \t]+(?:100\.123\.114\.16|@TAILNET_BIND@):443\b[^;]*;",
    re.M,
)
NGINX_T_FILE_RE = re.compile(r"^# configuration file (.+):$", re.M)
TAILNET_443_NEEDLE = re.compile(
    r"listen[ \t]+(?:100\.123\.114\.16|@TAILNET_BIND@):443\b"
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


def leftover_paths(nginx_t="", roots=()):
    """Every include nginx -T named plus on-disk leftovers.

    sites-enabled/mag-room.conf stays in the list even when rewrite_path
    follows it to sites-available. Live mag after #156 moved only conf.d
    because the enabled path was dropped.
    """
    found = []
    seen = set()
    for path in files_from_nginx_t(nginx_t) + files_from_trees(roots):
        for dest in (path, rewrite_path(path)):
            if dest and dest not in seen:
                seen.add(dest)
                found.append(dest)
    return found


def candidate_files(nginx_t="", roots=()):
    """Union of nginx -T include paths and on-disk leftover listens."""
    return leftover_paths(nginx_t, roots)


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
    # Public Funnel. HTTPS here is what publishes the ts.net name. A raw TCP
    # forward does not. nginx is moved off the Tailscale address first so
    # tailscaled can bind :443.
    _https_proxy(tcp, web, str(FUNNEL_PORT), FUNNEL_PROXY, only=True)
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
    if block.get("HTTPS") is not True:
        raise SystemExit("funnel port is not HTTPS")
    if block.get("TCPForward") or block.get("TerminateTLS") or block.get("ProxyProtocol"):
        raise SystemExit("funnel port is not a plain HTTPS proxy")
    handlers = (web.get(funnel_key()) or {}).get("Handlers") or {}
    if set(handlers) != {"/"} or _proxy(handlers["/"]["Proxy"]) != FUNNEL_PROXY:
        raise SystemExit("funnel port does not proxy to Gitea")
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
    raw = sys.stdin.read()
    if mode == "retarget":
        sys.stdout.write(retarget_nginx(raw))
        return 0
    if mode == "candidates":
        for path in candidate_files(raw, argv[2:]):
            sys.stdout.write(path + "\n")
        return 0
    if mode == "scan":
        roots = argv[2:]
        has_dump = bool(NGINX_T_FILE_RE.search(raw))
        if not has_dump and not roots:
            sys.stderr.write(
                "usage: merge-gitea-funnel.py scan [tree ...] < nginx-T-dump\n"
                "gitea funnel: scan needs an nginx -T dump or file arguments\n"
            )
            return 2
        leftover = leftover_paths(raw, roots)
        if leftover:
            sys.stderr.write(
                "gitea funnel: nginx still listens on 100.123.114.16:443 in:\n"
            )
            for path in leftover:
                sys.stderr.write(f"  {path}\n")
            return 1
        return 0
    current = json.loads(raw) if raw.strip() else {}
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
        "usage: merge-gitea-funnel.py merge|check|origin|retarget|candidates|scan"
    )


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv))
    except KeyError as exc:
        raise SystemExit(f"serve config missing {exc}") from None
