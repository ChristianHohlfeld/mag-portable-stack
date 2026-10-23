#!/usr/bin/env bash
# ops/gitea/bot-rights.sh against a fake Gitea CLI + API that, like Gitea,
# refuses POST /admin/users/{u}/orgs once allow_create_organization=false.
# Every bot ends locked down and owner of bots-<agent>; reruns are no-ops; one
# failing user does not stop the others; no token in the output; the
# throw-away admin is purged and left no org.
set -euo pipefail
root=$(cd -- "$(dirname "$0")/../.." && pwd)
tmp=$(mktemp -d)
pid=
trap '[[ -n $pid ]] && kill "$pid" 2>/dev/null; rm -rf "$tmp"' EXIT
cat > "$tmp/gitea" <<'SH'
#!/usr/bin/env bash
# fake: gitea admin user {list|create|generate-access-token|delete}
shift 2; cmd=$1; shift
case $cmd in
  list) printf 'ID Username Email\n1 chris c@x\n2 magbot-agent1 a\n3 magbot-agent3 b\n4 magbot-agentbroken d\n' ;;
  create) echo "create $2" >> "$FAKE_DIR/cli.log" ;;
  generate-access-token) echo 'tok-SECRET-value' ;;
  delete) echo "delete $2 $3" >> "$FAKE_DIR/cli.log" ;;
esac
SH
chmod +x "$tmp/gitea"
cat > "$tmp/fake.py" <<'PY'
import json, re, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
users = {u: {"admin": True, "restricted": False, "allow_create_organization": True}
         for u in ("magbot-agent1", "magbot-agent3")}
orgs = {"bots-agent1": {"owners": {"magbot-agent1"}}}  # pre-existing, already fine
log = open(sys.argv[2], "a")
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def reply(self, code, obj=None):
        b = b"" if obj is None else json.dumps(obj).encode()
        self.send_response(code); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def handle_any(self):
        if self.headers.get("Authorization") != "token tok-SECRET-value": return self.reply(401, {})
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        p, m = self.path[len("/api/v1"):], self.command
        if m != "GET": log.write("%s %s\n" % (m, p)); log.flush()
        if r := re.fullmatch(r"/admin/users/([^/]+)", p):
            if r[1] not in users: return self.reply(404, {})
            users[r[1]].update({k: body[k] for k in ("admin", "restricted", "allow_create_organization")}); return self.reply(200, {})
        if r := re.fullmatch(r"/admin/users/([^/]+)/orgs", p):
            return self.reply(500 if not users[r[1]]["allow_create_organization"] else 201, {})
        if p == "/orgs" and m == "POST":
            if body.get("visibility") != "private": return self.reply(422, {})
            orgs[body["username"]] = {"owners": {"admin"}}; return self.reply(201, {})
        if r := re.fullmatch(r"/orgs/([^/]+)", p):
            return self.reply(200 if r[1] in orgs else 404, {})
        if r := re.fullmatch(r"/orgs/([^/]+)/teams", p):
            return self.reply(200, [{"id": 7, "name": "Developers"}, {"id": list(orgs).index(r[1]) * 10 + 1, "name": "Owners"}])
        if r := re.fullmatch(r"/teams/(\d+)/members/([^/]+)", p):
            if r[2] not in users: return self.reply(404, {})
            list(orgs.values())[int(r[1]) // 10]["owners"].add(r[2]); return self.reply(204)
        if (r := re.fullmatch(r"/orgs/([^/]+)/members/([^/]+)", p)) and m == "DELETE":
            orgs[r[1]]["owners"].discard("admin"); return self.reply(204)
        if p == "/_state": return self.reply(200, {"users": users, "orgs": {k: sorted(v["owners"]) for k, v in orgs.items()}})
        self.reply(404, {})
    do_GET = do_POST = do_PATCH = do_PUT = do_DELETE = handle_any
HTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
PY
port=$(python3 -c 'import socket;s=socket.socket();s.bind(("127.0.0.1",0));print(s.getsockname()[1])')
python3 "$tmp/fake.py" "$port" "$tmp/api.log" &
pid=$!
for _ in $(seq 1 50); do curl -s -o /dev/null "http://127.0.0.1:$port/" && break; sleep 0.1; done
export FAKE_DIR=$tmp MAG_GITEA_CLI="$tmp/gitea" MAG_GITEA_API="http://127.0.0.1:$port/api/v1"
# magbot-agentbroken has no Gitea account: reported, others still applied, exit 1.
if bash "$root/ops/gitea/bot-rights.sh" > "$tmp/out1" 2>&1; then echo 'failing user not reported' >&2; exit 1; fi
grep -Fq 'ERROR: Gitea rights for magbot-agentbroken not applied' "$tmp/out1"
grep -Fq 'magbot-agent1: admin=false restricted=true org=bots-agent1 owner' "$tmp/out1"
grep -Fq 'magbot-agent3: admin=false restricted=true org=bots-agent3 owner' "$tmp/out1"
! grep -q '/admin/users/[^/]*/orgs' "$tmp/api.log"
state=$(curl -sS -H 'Authorization: token tok-SECRET-value' "http://127.0.0.1:$port/api/v1/_state")
jq -e '[.users[] | .admin == false and .restricted == true and .allow_create_organization == false] | all' <<<"$state" >/dev/null
jq -e '.orgs == {"bots-agent1": ["magbot-agent1"], "bots-agent3": ["magbot-agent3"]}' <<<"$state" >/dev/null
# Rerun for the existing bots: success, no org created, no error output.
: > "$tmp/api.log"
bash "$root/ops/gitea/bot-rights.sh" magbot-agent1 magbot-agent3 > "$tmp/out2" 2>&1
! grep -Eq 'ERROR|curl|HTTP' "$tmp/out2"
! grep -Fq 'POST /orgs' "$tmp/api.log"
[[ $(grep -c '^delete mag-rights-[0-9a-f]* --purge$' "$tmp/cli.log") == 2 ]]
if grep -q 'SECRET' "$tmp"/out*; then echo 'token leaked to output' >&2; exit 1; fi
echo 'Gitea bot rights PASS'
