#!/usr/bin/env bash
# ops/gitea/mirror-github.sh against a fake Gitea API: creates the mirror
# once, syncs on rerun, refuses a non-mirror repo, never leaks the tokens.
set -euo pipefail
root=$(cd -- "$(dirname "$0")/../.." && pwd)
tmp=$(mktemp -d)
pid=
trap '[[ -n $pid ]] && kill "$pid" 2>/dev/null; rm -rf "$tmp"' EXIT
printf 'gitea-SECRET-token\n' > "$tmp/gitea.token"
printf 'github-SECRET-token\n' > "$tmp/github.token"
chmod 0600 "$tmp/gitea.token" "$tmp/github.token"
cat > "$tmp/fake.py" <<'PY'
import json, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
state = {"repo": None}
log = open(sys.argv[2], "a")
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def reply(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def authed(self):
        return self.headers.get("Authorization") == "token gitea-SECRET-token"
    def do_GET(self):
        if not self.authed(): return self.reply(401, {"message": "no"})
        if self.path == "/api/v1/users/chris": return self.reply(200, {"login": "chris"})
        if self.path == "/api/v1/repos/chris/mag-portable-stack":
            return self.reply(200, state["repo"]) if state["repo"] else self.reply(404, {"message": "nf"})
        if self.path == "/api/v1/repos/chris/plain": return self.reply(200, {"mirror": False})
        self.reply(404, {"message": "nf"})
    def do_POST(self):
        if not self.authed(): return self.reply(401, {"message": "no"})
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        if self.path == "/api/v1/repos/migrate":
            req = json.loads(body)
            ok = (req["mirror"] is True and req["private"] is True and req["service"] == "github"
                  and req["auth_token"] == "github-SECRET-token" and req["repo_owner"] == "chris")
            log.write("migrate %s\n" % ok); log.flush()
            if not ok: return self.reply(422, {"message": "bad"})
            state["repo"] = {"mirror": True}
            return self.reply(201, {"mirror": True})
        if self.path == "/api/v1/repos/chris/mag-portable-stack/mirror-sync":
            log.write("sync\n"); log.flush(); return self.reply(200, {})
        self.reply(404, {"message": "nf"})
HTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
PY
port=$(python3 -c 'import socket;s=socket.socket();s.bind(("127.0.0.1",0));print(s.getsockname()[1])')
python3 "$tmp/fake.py" "$port" "$tmp/api.log" &
pid=$!
for _ in $(seq 1 50); do curl -s -o /dev/null "http://127.0.0.1:$port/" && break; sleep 0.1; done
export MAG_GITEA_URL="http://127.0.0.1:$port" MAG_GITEA_TOKEN_FILE="$tmp/gitea.token" \
  MAG_GITHUB_TOKEN_FILE="$tmp/github.token" MAG_MIRROR_ALLOW_ANY_OWNER=1
bash "$root/ops/gitea/mirror-github.sh" > "$tmp/out1" 2>&1
grep -Fq 'created pull mirror chris/mag-portable-stack' "$tmp/out1"
bash "$root/ops/gitea/mirror-github.sh" > "$tmp/out2" 2>&1
grep -Fq 'sync requested' "$tmp/out2"
[[ $(cat "$tmp/api.log") == $'migrate True\nsync' ]]
if MAG_MIRROR_NAME=plain bash "$root/ops/gitea/mirror-github.sh" > "$tmp/out3" 2>&1; then
  echo 'non-mirror repo was accepted' >&2; exit 1
fi
grep -Fq 'not a mirror' "$tmp/out3"
chmod 0644 "$tmp/github.token"
if bash "$root/ops/gitea/mirror-github.sh" > "$tmp/out4" 2>&1; then echo 'world-readable token accepted' >&2; exit 1; fi
if grep -q 'SECRET' "$tmp"/out*; then echo 'token leaked to output' >&2; exit 1; fi
echo 'Gitea mirror PASS'
