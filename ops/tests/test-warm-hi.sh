#!/usr/bin/env bash
# Warm-hi deploy gate: a hanging /health (prefill on the only slot) fails
# inside the budget; a warm idle slot passes. Does not start mag-llm.
set -euo pipefail
root=$(cd "$(dirname "$0")/../.." && pwd)
script=$root/ops/deploy-warm-hi.sh
bash -n "$script"
grep -q 'deploy-warm-hi.sh' "$root/ops/deploy.sh"
! grep -Eq 'systemctl (restart|stop|reload).*mag-llm' "$script" "$root/ops/deploy.sh"
tmp=$(mktemp -d)
trap 'kill "$pid" 2>/dev/null || true; rm -rf "$tmp"' EXIT
port=$(python3 -c 'import socket;s=socket.socket();s.bind(("127.0.0.1",0));print(s.getsockname()[1]);s.close()')
cat >"$tmp/srv.py" <<'PY'
import json, sys, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
mode = sys.argv[1]
class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass
    def do_GET(self):
        if mode == "hang" and self.path == "/health":
            time.sleep(30)
        if self.path == "/health":
            body = b'{"status":"ok"}'
        elif mode == "busy":
            body = json.dumps([{"id": 0, "is_processing": True, "n_prompt_tokens": 14000}]).encode()
        elif mode == "cold":
            body = json.dumps([{"id": 0, "is_processing": False, "state": 0}]).encode()
        else:
            body = json.dumps([{"id": 0, "is_processing": False, "n_prompt_tokens": 14000, "prompt": "cached"}]).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
ThreadingHTTPServer(("127.0.0.1", int(sys.argv[2])), H).serve_forever()
PY
python3 "$tmp/srv.py" warm "$port" &
pid=$!
sleep 0.2
start=$(date +%s%3N)
MAG_WARM_HI_PORT=$port MAG_WARM_HI_BUDGET_S=3 bash "$script" >/dev/null
echo "warm pass $(( $(date +%s%3N) - start ))ms"
kill "$pid"; wait "$pid" 2>/dev/null || true
python3 "$tmp/srv.py" hang "$port" &
pid=$!
sleep 0.2
start=$(date +%s%3N)
set +e
MAG_WARM_HI_PORT=$port MAG_WARM_HI_BUDGET_S=2 bash "$script" >"$tmp/out" 2>"$tmp/err"
rc=$?
set -e
ms=$(( $(date +%s%3N) - start ))
[[ "$rc" -eq 0 && "$ms" -lt 8000 ]]
grep -q 'slot busy' "$tmp/out"
grep -q 'Not restarting mag-llm' "$tmp/out"
echo "hang tolerated ${ms}ms"
kill "$pid"; wait "$pid" 2>/dev/null || true
python3 "$tmp/srv.py" busy "$port" &
pid=$!
sleep 0.2
set +e
MAG_WARM_HI_PORT=$port MAG_WARM_HI_BUDGET_S=3 bash "$script" >"$tmp/out" 2>"$tmp/err"
rc=$?
set -e
[[ "$rc" -eq 0 ]]
grep -q 'busy' "$tmp/out"
echo "busy slot accepted"
kill "$pid"; wait "$pid" 2>/dev/null || true
python3 "$tmp/srv.py" cold "$port" &
pid=$!
sleep 0.2
set +e
MAG_WARM_HI_PORT=$port MAG_WARM_HI_BUDGET_S=3 bash "$script" >/dev/null 2>"$tmp/err"
rc=$?
set -e
[[ "$rc" -ne 0 ]]
echo "cold slot rejected"
echo "warm hi gate PASS"
