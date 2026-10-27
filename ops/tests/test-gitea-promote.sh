#!/usr/bin/env bash
# Promote Gitea green and request a deploy. The API is a local fake. GitHub is
# refused. Auto-update follows the Gitea green ref, including the old
# "checkout is ahead of green" skip when Gitea green has not caught up.
set -euo pipefail
root=$(cd -- "$(dirname "$0")/../.." && pwd)
tmp=$(mktemp -d)
cleanup() {
  if [[ -s $tmp/pid ]]; then kill "$(cat "$tmp/pid")" 2>/dev/null || true; fi
  if [[ -s $tmp/hang.pid ]]; then kill "$(cat "$tmp/hang.pid")" 2>/dev/null || true; fi
  rm -rf "$tmp"
}
trap cleanup EXIT
chmod 755 "$root/ops/ci/fetch-gitea-green.sh" "$root/ops/ci/promote-gitea.sh" "$root/ops/ci/gitea-gate.sh"

sha() { printf '%040x' "$1"; }
token=fixture-token
export GITEA_TOKEN="$token"

cat > "$tmp/api.py" <<'PY'
#!/usr/bin/env python3
import json, os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
state = {
    "main": os.environ["MAIN_SHA"],
    "green": os.environ.get("GREEN_SHA") or None,
    "lie": os.environ.get("LIE") == "1",
    "fail_main": os.environ.get("FAIL_MAIN") == "1",
}
class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self, fmt, *args):
        return
    def _read(self):
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""
    def _json(self, code, obj):
        raw = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)
    def _auth(self):
        with open(os.environ["AUTH_LOG"], "a") as fh:
            fh.write((self.headers.get("Authorization") or "") + "\n")
    def do_GET(self):
        self._auth()
        if self.path.endswith("/branches/main"):
            if state["fail_main"]:
                return self._json(500, {"message": "no"})
            return self._json(200, {"commit": {"id": state["main"]}})
        if self.path.endswith("/git/refs/heads/green"):
            if state["lie"]:
                return self._json(200, {"object": {"sha": "0" * 40}})
            if not state["green"]:
                return self._json(404, {"message": "not found"})
            return self._json(200, {"object": {"sha": state["green"]}})
        if self.path.endswith("/status"):
            raw = open(os.environ["STATUS_FILE"]).read()
            return self._json(200, json.loads(raw or '{"statuses":[]}'))
        return self._json(404, {"message": "missing"})
    def do_POST(self):
        self._auth()
        state["green"] = json.loads(self._read() or b"{}")["sha"]
        self._json(201, {"object": {"sha": state["green"]}})
    def do_PATCH(self):
        self._auth()
        state["green"] = json.loads(self._read() or b"{}")["sha"]
        self._json(200, {"object": {"sha": state["green"]}})
srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
print(srv.server_address[1], flush=True)
srv.serve_forever()
PY

start_api() {
  : > "$tmp/auth"
  if [[ -n ${5:-} ]]; then printf '%s' "$5" > "$tmp/status.json"; else printf '%s' '{"statuses":[]}' > "$tmp/status.json"; fi
  MAIN_SHA="$1" GREEN_SHA="${2:-}" LIE="${3:-0}" FAIL_MAIN="${4:-0}" \
    STATUS_FILE="$tmp/status.json" AUTH_LOG="$tmp/auth" \
    python3 "$tmp/api.py" > "$tmp/port" &
  echo $! > "$tmp/pid"
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    [[ -s $tmp/port ]] && break
    sleep 0.05
  done
  echo "http://127.0.0.1:$(cat "$tmp/port")/api/v1"
}
stop_api() {
  if [[ -s $tmp/pid ]]; then kill "$(cat "$tmp/pid")" 2>/dev/null || true; wait "$(cat "$tmp/pid")" 2>/dev/null || true; fi
  rm -f "$tmp/pid" "$tmp/port"
}

promote() {
  local api=$1 want=$2 request=$3
  MAG_GITEA_API="$api" MAG_PROMOTE_SHA="$want" MAG_DEPLOY_REQUEST="$request" \
    MAG_GITEA_REPOSITORY=mag-ci/mag-portable-stack \
    bash "$root/ops/ci/promote-gitea.sh" > "$tmp/out" 2> "$tmp/err"
}

# Create green, confirm it, then write the deploy request. The token stays out of the logs.
api=$(start_api "$(sha 1)")
req=$tmp/request
promote "$api" "$(sha 1)" "$req"
grep -Fq "$(sha 1)" "$req"
! grep -Fq "$token" "$tmp/out" "$tmp/err" "$req"
grep -Fq "token ${token}" "$tmp/auth"
# Same SHA again still asks for deploy and does not move green backwards.
rm -f "$req"
promote "$api" "$(sha 1)" "$req"
grep -Fq "$(sha 1)" "$req"
stop_api

# A failed confirm does not request a deploy.
api=$(start_api "$(sha 2)" "" 1)
req=$tmp/request-lie
set +e
promote "$api" "$(sha 2)" "$req"
rc=$?
set -e
[[ $rc != 0 && ! -e $req ]]
stop_api

# Main lookup failure does not request a deploy.
api=$(start_api "$(sha 3)" "" 0 1)
req=$tmp/request-down
set +e
promote "$api" "$(sha 3)" "$req"
rc=$?
set -e
[[ $rc != 0 && ! -e $req ]]
stop_api

# Main moved on: skip cleanly, no deploy request.
api=$(start_api "$(sha 4)")
req=$tmp/request-moved
promote "$api" "$(sha 5)" "$req"
[[ ! -e $req ]]
stop_api

# GitHub is refused, and a GitHub Actions host skips without failing.
req=$tmp/request-gh
set +e
MAG_GITEA_API=https://api.github.com MAG_PROMOTE_SHA="$(sha 1)" MAG_DEPLOY_REQUEST="$req" \
  bash "$root/ops/ci/promote-gitea.sh" > "$tmp/out" 2> "$tmp/err"
rc=$?
set -e
[[ $rc != 0 && ! -e $req ]]
! grep -Fq "$token" "$tmp/err"
req=$tmp/request-skip
GITHUB_SERVER_URL=https://github.com MAG_PROMOTE_SHA="$(sha 1)" MAG_DEPLOY_REQUEST="$req" \
  bash "$root/ops/ci/promote-gitea.sh" > "$tmp/out" 2> "$tmp/err"
[[ ! -e $req ]]
grep -Fq 'skipping' "$tmp/err"

# Gate: empty statuses fall through to the local check. A red required status fails.
gate() {
  MAG_GITEA_API="$1" MAG_GATE_SHA="$(sha 9)" MAG_GITEA_REPOSITORY=mag-ci/mag-portable-stack \
    bash "$root/ops/ci/gitea-gate.sh" > "$tmp/gout" 2> "$tmp/gerr"
}
api=$(start_api "$(sha 9)" "" 0 0 '{"statuses":[]}')
gate "$api"
stop_api

body=$(jq -c '{statuses:[.contexts[]|{context:.,status:"success"}]}' "$root/ops/gitea-ci/policy.json")
api=$(start_api "$(sha 9)" "" 0 0 "$body")
gate "$api"
stop_api

body=$(jq -c '{statuses:[.contexts[]|{context:.,status:(if .=="speech" then "failure" else "success" end)}]}' "$root/ops/gitea-ci/policy.json")
api=$(start_api "$(sha 9)" "" 0 0 "$body")
set +e
gate "$api"
rc=$?
set -e
[[ $rc != 0 ]]
stop_api

body=$(jq -c '{statuses:[.contexts[]|{context:.,status:(if .=="native" then "pending" else "success" end)}]}' "$root/ops/gitea-ci/policy.json")
api=$(start_api "$(sha 9)" "" 0 0 "$body")
set +e
gate "$api"
rc=$?
set -e
[[ $rc != 0 ]]
stop_api

GITHUB_SERVER_URL=https://github.com bash "$root/ops/ci/gitea-gate.sh" > "$tmp/gout" 2> "$tmp/gerr"
grep -Fq 'skipping' "$tmp/gerr"
set +e
MAG_GITEA_API=https://api.github.com MAG_GATE_SHA="$(sha 9)" bash "$root/ops/ci/gitea-gate.sh" > "$tmp/gout" 2> "$tmp/gerr"
rc=$?
set -e
[[ $rc != 0 ]]

# Fetch follows Gitea green and refuses GitHub. A hung peer hits the hard timeout.
git init -q --bare -b main "$tmp/origin.git"
git clone -q "$tmp/origin.git" "$tmp/co"
git -C "$tmp/co" config user.email t@t
git -C "$tmp/co" config user.name t
git -C "$tmp/co" commit -q --allow-empty -m A
git -C "$tmp/co" push -q origin HEAD:main
git -C "$tmp/co" push -q origin HEAD:green
a=$(git -C "$tmp/co" rev-parse HEAD)
ref=$(MAG_GITEA_FETCH_URL="$tmp/origin.git" bash "$root/ops/ci/fetch-gitea-green.sh" --green-only "$tmp/co")
[[ $ref == refs/remotes/gitea/green && $(git -C "$tmp/co" rev-parse "$ref") == "$a" ]]
set +e
MAG_GITEA_FETCH_URL=https://github.com/example/example.git bash "$root/ops/ci/fetch-gitea-green.sh" --green-only "$tmp/co" > "$tmp/fout" 2> "$tmp/ferr"
rc=$?
set -e
[[ $rc != 0 ]]
set +e
MAG_GITEA_FETCH_URL='http://user:secret-token@127.0.0.1/x.git' bash "$root/ops/ci/fetch-gitea-green.sh" --green-only "$tmp/co" > "$tmp/fout" 2> "$tmp/ferr"
rc=$?
set -e
[[ $rc != 0 ]]
! grep -Fq secret-token "$tmp/ferr"

python3 - <<'PY' > "$tmp/hang.port" &
import socket
s = socket.socket()
s.bind(("127.0.0.1", 0))
s.listen(1)
print(s.getsockname()[1], flush=True)
while True:
    conn, _ = s.accept()
PY
echo $! > "$tmp/hang.pid"
for _ in 1 2 3 4 5 6 7 8 9 10; do
  [[ -s $tmp/hang.port ]] && break
  sleep 0.05
done
start=$(date +%s)
set +e
MAG_GITEA_FETCH_TIMEOUT=1 MAG_GITEA_FETCH_URL="http://127.0.0.1:$(cat "$tmp/hang.port")/mag-ci/mag-portable-stack.git" \
  bash "$root/ops/ci/fetch-gitea-green.sh" --green-only "$tmp/co" > "$tmp/fout" 2> "$tmp/ferr"
rc=$?
set -e
elapsed=$(( $(date +%s) - start ))
kill "$(cat "$tmp/hang.pid")" 2>/dev/null || true
wait "$(cat "$tmp/hang.pid")" 2>/dev/null || true
[[ $rc != 0 && $elapsed -lt 15 ]]

# Auto-update deploys Gitea green, fast-forwards when behind, and skips when ahead.
state=$tmp/state
mkdir -p "$state"
run_update() {
  MAG_AUTO_UPDATE_TEST=1 MAG_AUTO_UPDATE_ROOT="$tmp/co" MAG_AUTO_UPDATE_STATE="$state" \
    MAG_GITEA_FETCH_URL="$tmp/origin.git" \
    bash "$root/ops/auto-update/auto-update.sh" > "$tmp/uout" 2> "$tmp/uerr"
}
rm -f "$state/deployed-sha"
run_update
[[ $(cat "$tmp/uout") == "$a" ]]
printf '%s\n' "$a" > "$state/deployed-sha"
run_update
[[ ! -s $tmp/uout ]]
git -C "$tmp/co" commit -q --allow-empty -m B
git -C "$tmp/co" push -q origin HEAD:main
b=$(git -C "$tmp/co" rev-parse HEAD)
rm -f "$state/deployed-sha"
run_update
[[ ! -s $tmp/uout ]]
grep -Fq 'ahead of green' "$tmp/uerr"
git -C "$tmp/co" reset -q --hard "$a"
git -C "$tmp/origin.git" update-ref refs/heads/green "$b"
run_update
[[ $(cat "$tmp/uout") == "$b" && $(git -C "$tmp/co" rev-parse HEAD) == "$b" ]]

# Manual update names Gitea and does not fetch GitHub green.
grep -Fq 'fetch-gitea-green.sh' "$root/ops/update.sh"
! grep -Fq 'fetch origin green' "$root/ops/update.sh"
! grep -Fq 'origin/green' "$root/ops/update.sh"
# No new listener. Existing Gitea :3000 is only a client of the local API.
! grep -Eq 'ListenStream|^listen ' \
  "$root/ops/ci/promote-gitea.sh" "$root/ops/ci/gitea-gate.sh" "$root/ops/ci/fetch-gitea-green.sh" \
  "$root/.gitea/workflows/green.yml" "$root/ops/runners/mag-gitea-runner.service"
grep -Fq 'runs-on: mag' "$root/.gitea/workflows/green.yml"
grep -Fq 'timeout-minutes: 5' "$root/.gitea/workflows/green.yml"
grep -Fq 'timeout-minutes: 20' "$root/.gitea/workflows/green.yml"
grep -Fq 'pull_request:' "$root/.gitea/workflows/green.yml"
grep -Fq 'mag-github-runner.service) continue' "$root/ops/boot/ensure-boot.sh"
! grep -Fq 'mag-deploy-request.path) continue' "$root/ops/boot/ensure-boot.sh"
! grep -Fq 'disable --now mag-gitea-runner' "$root/ops/gitea-ci/install.sh"
! grep -Fq 'exec bash' "$root/ops/runners/install.sh"
echo 'Gitea promote/deploy PASS'
