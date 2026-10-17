#!/usr/bin/env python3
"""MAG Pages: read-only Gitea archive publisher; no hooks into mag-llm or Gitea DB."""
import argparse
import contextlib
import fcntl
import hashlib
import ipaddress
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import socket
import subprocess
import sys
import time
import tarfile
import tempfile
import urllib.error
import urllib.parse
import urllib.request

CONFIG = Path("/etc/mag-pages/config.json")
VISIBILITY = Path("/etc/mag-pages/visibility.json")
TOKEN = Path("/etc/mag-pages/gitea-token")
STATE = Path("/var/lib/mag-pages/state.json")
RELEASES = Path("/var/lib/mag-pages/releases")
CONF = Path("/etc/nginx/conf.d/mag-pages-generated.conf")
LOCK = Path("/run/lock/mag-pages.lock")
MAX_FILES = 10000
MAX_TOTAL = 100 * 1024 * 1024
MAX_FILE = 20 * 1024 * 1024
DOMAIN = re.compile(r"(?=.{4,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}\Z")
SHA = re.compile(r"[a-fA-F0-9]{40}\Z")
NAME = re.compile(r"[a-zA-Z0-9_.-]+\Z")


def atomic_text(path, text, mode=0o644):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".mag-pages-", delete=False, mode="w", encoding="utf-8") as f:
        tmp = Path(f.name)
        os.fchmod(f.fileno(), mode)
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def settings(path=CONFIG):
    c = json.loads(Path(path).read_text())
    assert c["gitea_url"].startswith("http://127.0.0.1:") or c["gitea_url"].startswith("http://[::1]:"), "Gitea API must be loopback"
    c["public_ip"] = str(ipaddress.IPv4Address(c["public_ip"]))
    c["public_dns_ip"] = str(ipaddress.IPv4Address(c.get("public_dns_ip", c["public_ip"])))
    c["tailnet_ip"] = str(ipaddress.IPv4Address(c["tailnet_ip"]))
    assert ipaddress.IPv4Address(c["tailnet_ip"]) in ipaddress.IPv4Network("100.64.0.0/10")
    assert c["public_ip"] != c["tailnet_ip"]
    assert DOMAIN.fullmatch(c["pages_domain"])
    assert DOMAIN.fullmatch(c["acme_email"].split("@")[-1])
    return c


def api(c, uri, *, binary=False):
    base = c["gitea_url"].rstrip("/")
    with urllib.request.urlopen(urllib.request.Request(
            base + "/api/v1/" + uri.lstrip("/"),
            headers={"Authorization": "token " + TOKEN.read_text().strip(),
                     "Accept": "application/json" if not binary else "application/octet-stream"}),
            timeout=60) as resp:
        if binary:
            with tempfile.TemporaryFile() as f:
                size = 0
                while True:
                    b = resp.read(1024 * 1024)
                    if not b:
                        break
                    size += len(b)
                    if size > 250 * 1024 * 1024:
                        raise ValueError("archive exceeds compressed size cap")
                    f.write(b)
                f.seek(0)
                return unpack(f)
        return json.load(resp)


def quote(segment):
    return urllib.parse.quote(segment, safe="")


def slug(full_name):
    name, owner = full_name.split("/", 1)[1], full_name.split("/", 1)[0]
    def clean(s):
        return re.sub(r"-+", "-", re.sub(r"[^a-z0-9-]", "-", s.lower())).strip("-")
    core = clean(name) + "--" + clean(owner)
    if not core:
        raise ValueError("empty repo slug")
    if len(core) > 63 or name.lower() != clean(name) or owner.lower() != clean(owner):
        core = core[:53].rstrip("-") + "-" + hashlib.sha256(full_name.encode()).hexdigest()[:8]
    return core


def unpack(archive):
    """Return safe (relative path, bytes) files; never unpack Git symlinks/devices."""
    found = {}
    size = 0
    count = 0
    with tarfile.open(fileobj=archive, mode="r:gz") as tf:
        for member in tf:
            parts = PurePosixPath(member.name).parts
            if len(parts) < 2:
                continue  # Gitea's archive prefix is the repository root
            path = PurePosixPath(*parts[1:])
            if any(p in ("..", ".") or p.startswith(".") for p in path.parts):
                continue
            if member.isdir():
                continue
            if not member.isfile():
                raise ValueError("archive includes symlink, hardlink or special file")
            count += 1
            size += member.size
            if count > MAX_FILES or size > MAX_TOTAL or member.size > MAX_FILE:
                raise ValueError("site exceeds file/size limits")
            f = tf.extractfile(member)
            if f is None:
                raise ValueError("invalid archive member")
            data = f.read(MAX_FILE + 1)
            if len(data) != member.size:
                raise ValueError("truncated archive member")
            found[str(path)] = data
    return found


def cname(files):
    b = files.pop("CNAME", None)
    if b is None:
        return None
    if len(b) > 254:
        raise ValueError("CNAME too long")
    s = b.decode("ascii").strip().lower().rstrip(".")
    if not DOMAIN.fullmatch(s):
        raise ValueError("CNAME must be a single valid DNS hostname")
    return s


def repo_pages(files, mode):
    if mode == "docs":
        return {p[5:]: v for p, v in files.items() if p.startswith("docs/") and len(p) > 5}
    return files


def repos(c):
    seen = set()
    page = 1
    while True:
        data = api(c, "repos/search?" + urllib.parse.urlencode({
            "page": page, "limit": 50, "private": "true", "sort": "id", "order": "asc"}))
        batch = data.get("data", [])
        for r in batch:
            if r["id"] not in seen:
                seen.add(r["id"])
                yield r
        if len(batch) < 50:
            break
        page += 1
        if page > 200:
            raise ValueError("repository list truncated; aborting to prevent unpublishing")


def resolve_branch(c, owner, repo, default_branch):
    base = "repos/" + quote(owner) + "/" + quote(repo) + "/branches/"
    try:
        branch = api(c, base + quote("gh-pages"))
        return "gh-pages", branch["commit"]["id"]
    except urllib.error.HTTPError as ex:
        if ex.code != 404:
            raise
    branch = api(c, base + quote(default_branch))
    return default_branch, branch["commit"]["id"]


def materialize(files, root):
    root.mkdir(parents=True, exist_ok=False)
    for rel, contents in files.items():
        # Defense in depth against unsafe paths.
        pp = PurePosixPath(rel)
        if not rel or pp.is_absolute() or ".." in pp.parts or any(p.startswith(".") for p in pp.parts):
            continue
        dst = root.joinpath(*pp.parts)
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(contents)
        dst.chmod(0o644)
    for p in root.rglob("*"):
        if p.is_dir():
            p.chmod(0o755)


def get_visibility(path=None):
    if path is None:
        path = VISIBILITY
    if not Path(path).exists():
        return {}
    data = json.loads(Path(path).read_text())
    if not isinstance(data, dict) or any(v not in ("public", "tailnet") for v in data.values()):
        raise ValueError("invalid visibility file")
    return data


def visibility_change(key, visibility, path=VISIBILITY):
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", key) or any(part in (".", "..") or part.startswith(".") for part in key.split("/")):
        raise ValueError("expected owner/repo")
    if visibility not in ("public", "tailnet"):
        raise ValueError("visibility must be public or tailnet")
    data = get_visibility(path)
    data[key] = visibility
    atomic_text(path, json.dumps(data, indent=2, sort_keys=True) + "\n", 0o600)


def dns_points_to_site(host, canonical, public_ip):
    # A custom CNAME must point to the MAG domain, or an apex must resolve to its public IP.
    # Command output is data, never evaluated or passed to shell.
    dig = shutil.which("dig")
    if not dig:
        raise RuntimeError("dig required for custom-domain validation")
    alias = subprocess.run([dig, "+short", "CNAME", host], text=True, capture_output=True, timeout=8, check=True).stdout
    cnames = {v.strip().lower().rstrip(".") for v in alias.splitlines()}
    if canonical.lower().rstrip(".") in cnames:
        return True
    ips = subprocess.run([dig, "+short", "A", host], text=True, capture_output=True, timeout=8, check=True).stdout
    return public_ip in {v.strip() for v in ips.splitlines()}


def cert_name(domain):
    return "mag-pages-" + hashlib.sha256(domain.encode()).hexdigest()[:16]


def custom_cert(c, domain):
    base = Path("/etc/letsencrypt/live") / cert_name(domain)
    crt, key = base / "fullchain.pem", base / "privkey.pem"
    if crt.is_file() and key.is_file():
        return str(crt), str(key)
    return None


def site_server(domain, root, ip_list, crt, key):
    listens = "\n".join("    listen " + ip + ":443 ssl;" for ip in ip_list)
    return (f"server {{\n{listens}\n    server_name {domain};\n"
            f"    ssl_certificate {crt};\n    ssl_certificate_key {key};\n"
            f"    root {root};\n    index index.html;\n"
            "    autoindex off;\n"
            "    add_header X-Content-Type-Options nosniff always;\n"
            "    add_header Referrer-Policy no-referrer always;\n"
            "    add_header Content-Security-Policy \"frame-ancestors 'none'\" always;\n"
            "    location ~ (^|/)\\. { return 404; }\n"
            "    location / { try_files $uri $uri/ =404; }\n"
            "}\n")


def render_nginx(c, state):
    cert = c["wildcard_cert"]
    key = c["wildcard_key"]
    public_ip, tailnet = c["public_ip"], c["tailnet_ip"]
    lines = ["# Generated by MAG Pages. Do not edit.", "server {",
             f"    listen {public_ip}:80;",
             "    server_name _;",
             "    location ^~ /.well-known/acme-challenge/ {",
             "        root /var/lib/mag-pages/acme;",
             "        default_type text/plain;",
             "    }",
             "    location / { return 404; }", "}", ""]
    # Prevent catch-all mismatched Host/SNI from reaching a private vhost.
    for ip in (public_ip,):
        lines.extend(["server {", f"    listen {ip}:443 ssl;",
                      "    server_name _;", f"    ssl_certificate {cert};",
                      f"    ssl_certificate_key {key};", "    return 444;", "}", ""])
    for keyid, s in sorted(state.items()):
        root = s["root"]
        if not Path(root).is_absolute() or Path(root).parent.parent != RELEASES or not re.fullmatch(r"[a-z0-9-]+/[a-f0-9]{40}", "/".join(Path(root).parts[-2:])):
            raise ValueError("unsafe release root")
        ips = [tailnet] if s["visibility"] == "tailnet" else [public_ip, tailnet]
        lines.append(site_server(s["domain"], root, ips, cert, key))
        if s.get("custom") and s["visibility"] == "public":
            certpair = custom_cert(c, s["custom"])
            if certpair:
                lines.append(site_server(s["custom"], root, [public_ip, tailnet], *certpair))
    return "\n".join(lines)


def publish_nginx(config, nginx=CONF):
    current = Path(nginx).read_text() if Path(nginx).exists() else None
    if current == config:
        return False
    atomic_text(nginx, config)
    try:
        subprocess.run(["nginx", "-t"], check=True, timeout=20, capture_output=True)
        subprocess.run(["systemctl", "reload", "nginx.service"], check=True, timeout=20, capture_output=True)
    except (subprocess.SubprocessError, OSError) as ex:
        if current is None:
            Path(nginx).unlink(missing_ok=True)
        else:
            atomic_text(nginx, current)
        raise RuntimeError("nginx validation/reload failed; previous config restored") from ex
    return True


def attempt_cert(c, custom, canonical):
    if custom_cert(c, custom):
        return
    if not dns_points_to_site(custom, canonical, c.get("public_dns_ip", c["public_ip"])):
        print(f"Custom domain {custom}: DNS not pointing at {canonical} / {c['public_ip']}; retry later", file=sys.stderr)
        return
    # HTTP-01 requires port 80 reachable through nginx. certbot stores renewal config on host.
    subprocess.run(["certbot", "certonly", "--webroot", "-w", "/var/lib/mag-pages/acme",
                    "--cert-name", cert_name(custom), "-d", custom, "--non-interactive",
                    "--agree-tos", "--email", c["acme_email"], "--keep-until-expiring"],
                   check=True, timeout=180)


def sync(c):
    visibility = get_visibility()
    previous = json.loads(STATE.read_text()) if STATE.exists() else {}
    state = {}
    custom_names = set()
    errors = []
    for r in repos(c):
        try:
            full = r["full_name"]
            owner, name = full.split("/", 1)
            if not NAME.fullmatch(owner) or not NAME.fullmatch(name):
                raise ValueError("invalid owner/repo")
            branch, sha = resolve_branch(c, owner, name, r["default_branch"])
            if not SHA.fullmatch(sha):
                raise ValueError("bad commit SHA")
            mode = "branch" if branch == "gh-pages" else "docs"
            key = slug(full)
            domain = key + "." + c["pages_domain"]
            if key in state:
                raise ValueError("site-name collision")
            site = previous.get(key)
            if site and site.get("sha") == sha and site.get("mode") == mode:
                candidate = dict(site)
            else:
                files = api(c, "repos/" + quote(owner) + "/" + quote(name) +
                            "/archive/" + quote(branch) + ".tar.gz", binary=True)
                files = repo_pages(files, mode)
                if not files:
                    continue
                custom = cname(files)
                if not files:
                    continue
                root = RELEASES / key / sha.lower()
                if not root.exists():
                    root.parent.mkdir(parents=True, exist_ok=True)
                    tmp = Path(tempfile.mkdtemp(prefix=".stage-", dir=root.parent))
                    try:
                        # Materialize under a new directory to avoid serving partial uploads.
                        materialize(files, tmp / "site")
                        os.replace(tmp / "site", root)
                    finally:
                        shutil.rmtree(tmp, ignore_errors=True)
                candidate = {"sha": sha.lower(), "mode": mode, "root": str(root), "domain": domain, "custom": custom}
            candidate["visibility"] = visibility.get(full, "tailnet")
            candidate["domain"] = domain
            if candidate.get("custom"):
                custom = candidate["custom"]
                if custom.endswith("." + c["pages_domain"]) or custom == c["pages_domain"] or custom in custom_names:
                    raise ValueError("invalid/conflicting custom domain")
                custom_names.add(custom)
            state[key] = candidate
        except urllib.error.HTTPError as ex:
            if ex.code == 404:
                # No branch/docs -> no publication. Authentication/list failures fail closed.
                print(f"{r.get('full_name')}: absent branch/content ({ex.code})", file=sys.stderr)
                continue
            errors.append(f"{r.get('full_name')}: Gitea HTTP {ex.code}")
        except Exception as ex:
            errors.append(f"{r.get('full_name')}: {ex}")
    if errors:
        # Keep last known published configuration on any repository error.
        raise RuntimeError("sync aborted; old nginx sites preserved: " + "; ".join(errors))
    publish_nginx(render_nginx(c, state))
    atomic_text(STATE, json.dumps(state, indent=2, sort_keys=True) + "\n", 0o600)
    for s in state.values():
        if (s["visibility"] == "public" and s.get("custom") and
                not custom_cert(c, s["custom"]) and
                time.time() - s.get("cert_attempt_at", 0) >= 3600):
            s["cert_attempt_at"] = time.time()  # prevent rate-limit loops on DNS/ACME failures
            try:
                attempt_cert(c, s["custom"], s["domain"])
            except (OSError, subprocess.SubprocessError) as ex:
                print(f"Custom cert for {s['custom']} pending: {ex}", file=sys.stderr)
    atomic_text(STATE, json.dumps(state, indent=2, sort_keys=True) + "\n", 0o600)
    publish_nginx(render_nginx(c, state))
    print(f"MAG Pages: {len(state)} site(s); no mag-llm changes")


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("sync")
    pub = sub.add_parser("visibility")
    pub.add_argument("repository", help="owner/repo")
    pub.add_argument("access", choices=["public", "tailnet"])
    a = p.parse_args()
    if os.geteuid() != 0:
        p.error("run as root (configuration and Gitea token are root-only)")
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    with open(LOCK, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if a.command == "visibility":
            visibility_change(a.repository, a.access)
            print(f"{a.repository}: {a.access}; sync within one minute")
        else:
            sync(settings())


if __name__ == "__main__":
    main()
