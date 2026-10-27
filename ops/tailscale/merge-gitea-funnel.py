#!/usr/bin/env python3
"""Merge a tailscaled ServeConfig so only Gitea is published with Funnel.

Tailnet serves stay in place. AllowFunnel is rewritten to the single Gitea
endpoint. Unknown tailnet handlers and service entries are kept; their funnel
flags are not.
"""
import json
import sys

NODE = "mag.tail3e02ae.ts.net"
FUNNEL_PORT = 443
FUNNEL_TARGET = "http://127.0.0.1:3010"
FUNNEL_ORIGIN = "https://mag.tail3e02ae.ts.net"
# Existing tailnet-only serves. These ports are not funnel-capable targets
# for this change: 8443 is already in use, and the room stays on 8446.
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
    wanted = {str(FUNNEL_PORT): FUNNEL_TARGET, **TAILNET}
    for port, proxy in wanted.items():
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
        handlers = server.get("Handlers") or {}
        if not isinstance(handlers, dict):
            handlers = {}
        handlers["/"] = {"Proxy": proxy}
        server["Handlers"] = handlers
        web[hp] = server
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
    got = _proxy(web[funnel_key()]["Handlers"]["/"]["Proxy"])
    if got != FUNNEL_TARGET:
        raise SystemExit("funnel target is not the Gitea nginx listener")
    if tcp.get(str(FUNNEL_PORT), {}).get("HTTPS") is not True:
        raise SystemExit("funnel port is not HTTPS")
    for port, proxy in TAILNET.items():
        if _proxy(web[hostport(port)]["Handlers"]["/"]["Proxy"]) != proxy:
            raise SystemExit(f"tailnet serve :{port} changed")
        if tcp.get(port, {}).get("HTTPS") is not True:
            raise SystemExit(f"tailnet port {port} is not HTTPS")
        if hostport(port) in allow:
            raise SystemExit(f"tailnet port {port} is funneled")
    for key, on in allow.items():
        if on and key != funnel_key():
            raise SystemExit("a non-Gitea endpoint is funneled")


def main(argv):
    mode = argv[1] if len(argv) > 1 else "merge"
    raw = sys.stdin.read().strip()
    current = json.loads(raw) if raw else {}
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
    raise SystemExit("usage: merge-gitea-funnel.py merge|check|origin")


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv))
    except KeyError as exc:
        raise SystemExit(f"serve config missing {exc}") from None
