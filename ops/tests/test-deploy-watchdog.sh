#!/usr/bin/env bash
# A hanging probe must not keep /run/lock/mag-private-deploy.lock. The deploy
# watchdog kills that process group at the deadline and the EXIT/rollback path
# still runs. Does not start mag-llm.
set -euo pipefail
root=$(cd -- "$(dirname "$0")/../.." && pwd)
bash -n "$root/ops/deploy-watchdog.sh"
bash -n "$root/ops/deploy.sh"
bash -n "$root/ops/ci/promote-green.sh"
shellcheck "$root/ops/deploy-watchdog.sh" "$root/ops/ci/promote-green.sh"
grep -q 'ops/deploy-watchdog.sh' "$root/ops/deploy.sh"
grep -q 'deploy_watchdog_arm' "$root/ops/deploy.sh"
grep -q 'deploy_lock_cloexec' "$root/ops/deploy.sh"
grep -q 'deploy_run' "$root/ops/deploy.sh"
grep -q '/run/lock/mag-private-deploy.lock' "$root/ops/deploy.sh"
grep -q 'trap deploy_exit EXIT' "$root/ops/deploy.sh"
! grep -Eq 'systemctl (restart|stop|reload).*mag-llm' "$root/ops/deploy.sh" "$root/ops/deploy-watchdog.sh"
# SC2016 is the jq program; the disable must sit on that assignment.
grep -q 'disable=SC2016' "$root/ops/ci/promote-green.sh"

tmp=$(mktemp -d)
trap 'kill "$srv" 2>/dev/null || true; rm -rf "$tmp"' EXIT
port=$(python3 -c 'import socket;s=socket.socket();s.bind(("127.0.0.1",0));print(s.getsockname()[1]);s.close()')
python3 - "$port" >"$tmp/srv.log" 2>&1 <<'PY' &
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
class H(BaseHTTPRequestHandler):
    def do_GET(self):
        # curl --retry treats 429 as transient. Retry-After is a sleep between
        # transfers and is not covered by --max-time.
        self.send_response(429)
        self.send_header("Retry-After", "600")
        self.send_header("Content-Length", "0")
        self.end_headers()
    def log_message(self, *a):
        pass
ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
PY
srv=$!
for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do
  python3 -c 'import socket,sys;s=socket.create_connection(("127.0.0.1",int(sys.argv[1])),1);s.close()' "$port" 2>/dev/null && break
  sleep 0.05
done
start=$(date +%s)
set +e
MAG_DEPLOY_DEADLINE_S=3 MAG_DEPLOY_ROLLBACK_GRACE_S=8 \
  bash -c '
    set -euo pipefail
    source "$1"
    deployment_started=1
    mark=$3
    rollback() { printf "rollback\n" >"$mark"; }
    exec 8>"$2"
    flock -n 8 || { echo "lock busy before deploy" >&2; exit 1; }
    deploy_lock_cloexec 8
    deploy_watchdog_arm "$MAG_DEPLOY_DEADLINE_S"
    trap deploy_exit EXIT
    trap "exit 143" TERM
    trap "exit 130" INT
    if deploy_run 3 -- bash -c "test -e /proc/self/fd/8"; then
      echo "child inherited the deploy lock fd" >&2
      exit 2
    fi
    # Hanging probe. Without the deadline, curl sleeps on Retry-After: 600.
    deploy_run 120 -- curl -sS --retry 2 --retry-max-time 0 --max-time 5 -o /dev/null \
      "http://127.0.0.1:'"$port"'/probe"
    exit 1
  ' _ "$root/ops/deploy-watchdog.sh" "$tmp/lock" "$tmp/mark"
rc=$?
set -e
elapsed=$(( $(date +%s) - start ))
echo "deploy ended rc=$rc in ${elapsed}s"
[[ $rc -ne 0 ]]
(( elapsed < 8 ))
[[ -f $tmp/mark ]]
grep -q rollback "$tmp/mark"
exec 9>"$tmp/lock"
flock -n 9 || { echo 'deploy lock still held' >&2; exit 1; }
if ps -eo args= | grep -F "127.0.0.1:${port}/probe" | grep -v grep | grep -q .; then
  echo 'orphan probe still running' >&2
  ps -eo args= | grep -F "127.0.0.1:${port}/probe" | grep -v grep >&2 || true
  exit 1
fi
echo 'deploy watchdog: deadline held, rollback ran, lock released'
