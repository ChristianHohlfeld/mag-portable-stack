#!/usr/bin/env python3
"""git-memory: versionierter Bot-Speicher auf Git (MCP-Server + CLI).

Werkzeuge (wie vom Bot vorgeschlagen):
  remember(key, value)  schreibt entries/<key>.md, committet und pusht automatisch
  retrieve(key)         liest die aktuelle Version
  history(key)          Versionen eines Eintrags (git log)
  search(query)         Volltextsuche ueber alle Eintraege

Speicher: Git-Repo $GITMEM_DIR (Default ~/memory), Backup-Remote auf Gitea
($GITMEM_REMOTE, Default http://<gitea>/<user>/memory.git aus ~/.git-credentials;
das Repo wird bei Bedarf privat ueber die Gitea-API angelegt). Zugangsdaten werden
nur aus ~/.git-credentials gelesen und nie ausgegeben.

Aufruf:
  gitmem.py              MCP ueber stdio (JSON-RPC 2.0, Protokoll 2024-11-05)
  gitmem.py remember KEY VALUE|-   /  retrieve KEY  /  history KEY  /  search QUERY
Nur Python-Standardbibliothek + git.
"""
import base64
import json
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request

HOME = os.path.expanduser("~")
DIR = os.environ.get("GITMEM_DIR", os.path.join(HOME, "memory"))
CRED = os.environ.get("GITMEM_CREDENTIALS", os.path.join(HOME, ".git-credentials"))
REPO_NAME = os.environ.get("GITMEM_REPO", "memory")
MAX_VALUE = 200_000
VERSION = "1.0.0"


class MemError(Exception):
    pass


def _git(*args, check=True):
    r = subprocess.run(["git", "-C", DIR, *args], capture_output=True, text=True, timeout=60)
    if check and r.returncode:
        raise MemError(f"git {args[0]} fehlgeschlagen: {(r.stderr or r.stdout).strip()[:300]}")
    return r.stdout


def _credential():
    """(base_url, user, password) der ersten Zeile aus ~/.git-credentials oder None."""
    try:
        with open(CRED) as fh:
            for line in fh:
                u = urllib.parse.urlsplit(line.strip())
                if u.scheme in ("http", "https") and u.username:
                    # Host kann URL-kodiert gespeichert sein (192.168.122.1%3a3001).
                    host = urllib.parse.unquote(u.netloc.rsplit("@", 1)[-1])
                    base = f"{u.scheme}://{host}"
                    return base, urllib.parse.unquote(u.username), urllib.parse.unquote(u.password or "")
    except OSError:
        pass
    return None


def _remote():
    env = os.environ.get("GITMEM_REMOTE")
    if env:
        return env
    c = _credential()
    return f"{c[0]}/{c[1]}/{REPO_NAME}.git" if c else None


def _ensure_gitea_repo():
    c = _credential()
    if not c or os.environ.get("GITMEM_REMOTE"):
        return
    base, user, pw = c
    auth = "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()
    req = urllib.request.Request(f"{base}/api/v1/repos/{user}/{REPO_NAME}", headers={"Authorization": auth})
    try:
        urllib.request.urlopen(req, timeout=10)
        return
    except urllib.error.HTTPError as e:
        if e.code != 404:
            return
    except OSError:
        return
    body = json.dumps({"name": REPO_NAME, "private": True, "auto_init": False,
                       "description": "git-memory des Bots (versionierter Speicher)"}).encode()
    req = urllib.request.Request(f"{base}/api/v1/user/repos", data=body, method="POST",
                                 headers={"Authorization": auth, "Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=10)
    except OSError:
        pass


def _ensure_repo():
    if not os.path.isdir(os.path.join(DIR, ".git")):
        os.makedirs(DIR, exist_ok=True)
        _git("init", "-q", "-b", "main")
        if not _git("config", "user.name", check=False).strip():
            _git("config", "user.name", "git-memory")
            _git("config", "user.email", "git-memory@mag.local")
        os.makedirs(os.path.join(DIR, "entries"), exist_ok=True)
        with open(os.path.join(DIR, "README.md"), "w") as fh:
            fh.write("# git-memory\n\nEin Eintrag pro Datei unter entries/<key>.md. "
                     "Verwaltet von gitmem.py (remember/retrieve/history/search).\n")
        _git("add", "README.md")
        _git("commit", "-q", "-m", "git-memory: init")
    remote = _remote()
    if remote and _git("remote", check=False).strip() and \
            _git("remote", "get-url", "origin", check=False).strip() != remote:
        _git("remote", "set-url", "origin", remote)  # z. B. korrigierte Gitea-URL
        _ensure_gitea_repo()
    if remote and not _git("remote", check=False).strip():
        _ensure_gitea_repo()
        _git("remote", "add", "origin", remote)
        # vorhandenen Stand vom Backup holen (z. B. nach VM-Neuaufbau)
        if _git("fetch", "-q", "origin", "main", check=False) is not None:
            if _git("rev-parse", "-q", "--verify", "origin/main", check=False).strip():
                _git("merge", "-q", "--allow-unrelated-histories", "-X", "theirs",
                     "-m", "git-memory: Backup uebernommen", "origin/main", check=False)


def _key(key):
    k = re.sub(r"[^A-Za-z0-9._-]+", "_", (key or "").strip()).strip("._")[:120]
    if not k:
        raise MemError("Leerer oder ungueltiger key")
    return k


def _path(k):
    return os.path.join("entries", k + ".md")


def _push():
    if not _git("remote", check=False).strip():
        return "kein Remote konfiguriert (nur lokal)"
    r = subprocess.run(["git", "-C", DIR, "push", "-q", "-u", "origin", "main"],
                       capture_output=True, text=True, timeout=60)
    if r.returncode:
        return "Push fehlgeschlagen, lokal gespeichert (naechstes remember pusht erneut)"
    return "gepusht"


def remember(key, value):
    _ensure_repo()
    k = _key(key)
    value = "" if value is None else str(value)
    if len(value) > MAX_VALUE:
        raise MemError("value zu gross (max 200 kB)")
    full = os.path.join(DIR, _path(k))
    os.makedirs(os.path.dirname(full), exist_ok=True)
    old = open(full).read() if os.path.exists(full) else None
    if old == value:
        return f"{k}: unveraendert ({_push()})"
    with open(full, "w") as fh:
        fh.write(value)
    _git("add", _path(k))
    _git("commit", "-q", "-m", f"remember {k}")
    rev = _git("rev-parse", "--short", "HEAD").strip()
    return f"{k}: {'aktualisiert' if old is not None else 'gespeichert'} @ {rev} ({_push()})"


def retrieve(key):
    _ensure_repo()
    k = _key(key)
    full = os.path.join(DIR, _path(k))
    if not os.path.exists(full):
        raise MemError(f"Kein Eintrag '{k}'")
    return open(full).read()


def history(key, limit=20):
    _ensure_repo()
    k = _key(key)
    out = _git("log", f"-n{int(limit)}", "--date=iso-strict", "--format=%h %ad %s", "--", _path(k)).strip()
    if not out:
        raise MemError(f"Keine Historie fuer '{k}'")
    lines = []
    for row in out.splitlines():
        h = row.split(" ", 1)[0]
        val = _git("show", f"{h}:{_path(k)}", check=False)
        lines.append(f"{row}\n    {val.strip()[:200]!r}" if val else f"{row}\n    (geloescht)")
    return "\n".join(lines)


def search(query, limit=20):
    _ensure_repo()
    q = (query or "").strip()
    if not q:
        raise MemError("Leere Suche")
    hits = []
    d = os.path.join(DIR, "entries")
    for name in sorted(os.listdir(d)) if os.path.isdir(d) else []:
        if not name.endswith(".md"):
            continue
        k = name[:-3]
        text = open(os.path.join(d, name), errors="replace").read()
        if q.lower() in k.lower() or q.lower() in text.lower():
            i = text.lower().find(q.lower())
            snip = text[max(0, i - 60): i + 140] if i >= 0 else text[:200]
            hits.append(f"{k}: {snip.strip()!r}")
            if len(hits) >= limit:
                break
    return "\n".join(hits) if hits else "Keine Treffer"


TOOLS = [
    {"name": "remember", "description": "Wissen dauerhaft und versioniert speichern (commit + push auf Gitea).",
     "inputSchema": {"type": "object", "properties": {"key": {"type": "string"}, "value": {"type": "string"}},
                     "required": ["key", "value"], "additionalProperties": False}},
    {"name": "retrieve", "description": "Aktuelle Version eines Eintrags lesen.",
     "inputSchema": {"type": "object", "properties": {"key": {"type": "string"}},
                     "required": ["key"], "additionalProperties": False}},
    {"name": "history", "description": "Versionen eines Eintrags (git log) mit Inhalt.",
     "inputSchema": {"type": "object", "properties": {"key": {"type": "string"}, "limit": {"type": "integer"}},
                     "required": ["key"], "additionalProperties": False}},
    {"name": "search", "description": "Volltextsuche ueber alle Eintraege (Key und Inhalt).",
     "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}},
                     "required": ["query"], "additionalProperties": False}},
]
FUNCS = {"remember": remember, "retrieve": retrieve, "history": history, "search": search}


def handle(msg):
    mid, method, params = msg.get("id"), msg.get("method"), msg.get("params") or {}
    if method == "initialize":
        res = {"protocolVersion": params.get("protocolVersion", "2024-11-05"),
               "capabilities": {"tools": {"listChanged": False}},
               "serverInfo": {"name": "git-memory", "version": VERSION}}
    elif method == "ping":
        res = {}
    elif method == "tools/list":
        res = {"tools": TOOLS}
    elif method == "tools/call":
        name, args = params.get("name"), params.get("arguments") or {}
        if name not in FUNCS:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": f"Unbekanntes Tool {name}"}}
        try:
            text, err = FUNCS[name](**args), False
        except (MemError, TypeError, OSError, subprocess.SubprocessError) as e:
            text, err = str(e), True
        res = {"content": [{"type": "text", "text": text}], "isError": err}
    elif mid is None:
        return None  # Notification (z. B. notifications/initialized)
    else:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"Methode {method} unbekannt"}}
    return {"jsonrpc": "2.0", "id": mid, "result": res}


def serve():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            out = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
        else:
            out = handle(msg)
        if out is not None:
            sys.stdout.write(json.dumps(out, ensure_ascii=False) + "\n")
            sys.stdout.flush()


def cli(argv):
    cmd, rest = argv[0], argv[1:]
    try:
        if cmd == "remember" and len(rest) >= 1:
            val = sys.stdin.read() if len(rest) == 1 or rest[1] == "-" else " ".join(rest[1:])
            print(remember(rest[0], val))
        elif cmd in ("retrieve", "history") and len(rest) == 1:
            print(FUNCS[cmd](rest[0]))
        elif cmd == "search" and rest:
            print(search(" ".join(rest)))
        else:
            print(__doc__)
            return 2
    except MemError as e:
        print(f"Fehler: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(cli(sys.argv[1:]) if len(sys.argv) > 1 else serve())
