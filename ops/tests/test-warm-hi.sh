#!/usr/bin/env bash
# mag-test-timeout: 180
if [ -z "${MAG_TEST_DEADLINE_ARMED:-}" ]; then
  export MAG_TEST_DEADLINE_ARMED=1
  exec timeout --kill-after=5s 180s bash "$0" "$@"
fi
# Warm-hi deploy gate: both bots, slots JSON on stdin, fail if a first hi
# would take 5s or more. Does not start mag-llm.
set -euo pipefail
root=$(cd "$(dirname "$0")/../.." && pwd)
script=$root/ops/deploy-warm-hi.sh
bash -n "$script"
grep -q 'deploy-warm-hi.sh' "$root/ops/deploy.sh"
! grep -q 'keeping this release' "$root/ops/deploy.sh"
grep -q 'FAIL: warm hi gate failed' "$root/ops/deploy.sh"
awk '
  /deploy-warm-hi\.sh/ {gate=1}
  gate && /exit 1/ {found=1; exit}
  gate && /deployment_started=0/ {exit 1}
  END {exit found?0:1}
' "$root/ops/deploy.sh"
! grep -Eq 'systemctl (restart|stop|reload).*mag-llm' "$script" "$root/ops/deploy.sh"
grep -q 'sys.stdin.read' "$script"
! grep -Eq 'python3 -c .*"\$slots"' "$script"
grep -q '2048' "$script"
grep -q 'MAG_WARM_HI_REQUIRE_ROOM' "$script"
grep -q 'rolling back this release' "$root/ops/deploy.sh"
! grep -q 'keeping this release' "$root/ops/deploy.sh"
tmp=$(mktemp -d)
trap 'kill "$pid" "$pid2" 2>/dev/null || true; rm -rf "$tmp"' EXIT
free_port() { python3 -c 'import socket;s=socket.socket();s.bind(("127.0.0.1",0));print(s.getsockname()[1]);s.close()'; }
port=$(free_port)
port2=$(free_port)
cat >"$tmp/srv.py" <<'PY'
import json, sys, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
mode = sys.argv[1]
log_path = sys.argv[3] if len(sys.argv) > 3 else ""
# A production /slots body is hundreds of KB; the old gate passed it as argv.
huge = "x" * 200000
hits = {"cancel": 0, "restore": 0, "chat": 0, "slots": 0}
first_slots = {"t": None}
def note(line):
    if log_path:
        with open(log_path, "a") as fh:
            fh.write(line + "\n")
class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass
    def _json(self, body):
        raw = body if isinstance(body, bytes) else body.encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)
    def slots_body(self):
        hits["slots"] += 1
        if mode == "busy":
            return [{"id": 0, "is_processing": True, "n_prompt_tokens": 14000}]
        if mode in ("cold", "ik", "ik-slow"):
            # ik-llama on :8191: state only, no n_past / n_prompt_tokens / n_cached_tokens.
            return [{"id": 0, "state": 0}]
        if mode == "stub":
            # n_decoded / a 12-token stub used to print "prefix resident".
            return [{"id": 0, "is_processing": False, "n_prompt_tokens": 12,
                     "n_decoded": 4, "state": 1, "prompt": "x"}]
        if mode == "huge":
            return [{"id": 0, "is_processing": False, "n_prompt_tokens": 14000, "prompt": huge}]
        if mode == "prefill":
            if hits["restore"]:
                return [{"id": 0, "is_processing": False, "n_prompt_tokens": 14000, "prompt": "cached"}]
            return [{"id": 0, "is_processing": True, "n_prompt_tokens": 4096}]
        if mode == "prefill-ik":
            if hits["restore"]:
                return [{"id": 0, "state": 0}]
            return [{"id": 0, "is_processing": True, "n_prompt_tokens": 4096}]
        if mode == "prefill-wait":
            now = time.monotonic()
            if first_slots["t"] is None:
                first_slots["t"] = now
            if now - first_slots["t"] < 0.4:
                return [{"id": 0, "is_processing": True, "n_prompt_tokens": 4096}]
            return [{"id": 0, "is_processing": False, "n_prompt_tokens": 14000, "prompt": "cached"}]
        return [{"id": 0, "is_processing": False, "n_prompt_tokens": 14000, "prompt": "cached"}]
    def do_GET(self):
        note("GET " + self.path)
        if mode == "hang" and self.path == "/health":
            time.sleep(30)
        if mode == "slow" and self.path == "/health":
            time.sleep(6)
        if self.path == "/health":
            body = b'{"status":"ok"}'
        elif self.path.split("?", 1)[0] == "/slots":
            body = json.dumps(self.slots_body()).encode()
        else:
            body = b"{}"
        self._json(body)
    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(n)
        path = self.path.split("?", 1)[0]
        note("POST " + self.path)
        if "action=cancel" in self.path:
            hits["cancel"] += 1
        elif "action=restore" in self.path:
            hits["restore"] += 1
        elif path == "/v1/chat/completions":
            hits["chat"] += 1
            if mode == "ik-slow":
                time.sleep(6)
            body = b'data: {"choices":[{"delta":{"content":"H"}}]}\n\ndata: [DONE]\n\n'
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self._json(b"{}")
ThreadingHTTPServer(("127.0.0.1", int(sys.argv[2])), H).serve_forever()
PY
python3 "$tmp/srv.py" warm "$port" &
pid=$!
sleep 0.2
start=$(date +%s%3N)
MAG_WARM_HI_PORT=$port MAG_WARM_HI_BUDGET_S=3 MAG_WARM_HI_ANSWER_MS=5000 bash "$script" >/dev/null
echo "warm pass $(( $(date +%s%3N) - start ))ms"
kill "$pid"; wait "$pid" 2>/dev/null || true

python3 "$tmp/srv.py" huge "$port" &
pid=$!
sleep 0.2
MAG_WARM_HI_PORT=$port MAG_WARM_HI_BUDGET_S=3 bash "$script" >/dev/null
echo "huge slots stdin pass"
kill "$pid"; wait "$pid" 2>/dev/null || true

python3 "$tmp/srv.py" hang "$port" &
pid=$!
sleep 0.2
start=$(date +%s%3N)
set +e
MAG_WARM_HI_PORT=$port MAG_WARM_HI_BUDGET_S=2 MAG_WARM_HI_ANSWER_MS=5000 bash "$script" >/dev/null 2>"$tmp/err"
rc=$?
set -e
ms=$(( $(date +%s%3N) - start ))
[[ "$rc" -ne 0 && "$ms" -lt 8000 ]]
grep -q 'Not restarting mag-llm' "$tmp/err"
echo "hang rejected ${ms}ms"
kill "$pid"; wait "$pid" 2>/dev/null || true

python3 "$tmp/srv.py" slow "$port" &
pid=$!
sleep 0.2
start=$(date +%s%3N)
set +e
MAG_WARM_HI_PORT=$port MAG_WARM_HI_BUDGET_S=8 MAG_WARM_HI_ANSWER_MS=5000 bash "$script" >/dev/null 2>"$tmp/err"
rc=$?
set -e
ms=$(( $(date +%s%3N) - start ))
[[ "$rc" -ne 0 && "$ms" -lt 8000 ]]
grep -q 'Not restarting mag-llm' "$tmp/err"
echo "slow health (>=5s) rejected ${ms}ms"
kill "$pid"; wait "$pid" 2>/dev/null || true

python3 "$tmp/srv.py" busy "$port" &
pid=$!
sleep 0.2
set +e
MAG_WARM_HI_PORT=$port MAG_WARM_HI_BUDGET_S=3 bash "$script" >"$tmp/out" 2>"$tmp/err"
rc=$?
set -e
[[ "$rc" -ne 0 ]]
grep -q 'Not restarting mag-llm' "$tmp/err"
echo "busy slot rejected"
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
kill "$pid"; wait "$pid" 2>/dev/null || true

python3 "$tmp/srv.py" warm "$port" &
pid=$!
python3 "$tmp/srv.py" warm "$port2" &
pid2=$!
sleep 0.2
MAG_WARM_HI_PORTS="$port,$port2" MAG_WARM_HI_BUDGET_S=3 bash "$script" | grep -q '2 bot'
echo "both bots pass"
kill "$pid" "$pid2"; wait "$pid" "$pid2" 2>/dev/null || true
pid=; pid2=

python3 "$tmp/srv.py" warm "$port" &
pid=$!
python3 "$tmp/srv.py" cold "$port2" &
pid2=$!
sleep 0.2
set +e
MAG_WARM_HI_PORTS="$port,$port2" MAG_WARM_HI_BUDGET_S=3 bash "$script" >/dev/null 2>"$tmp/err"
rc=$?
set -e
[[ "$rc" -ne 0 ]]
echo "second bot cold rejected"
kill "$pid" "$pid2"; wait "$pid" "$pid2" 2>/dev/null || true
pid=; pid2=

python3 "$tmp/srv.py" stub "$port" &
pid=$!
sleep 0.2
set +e
MAG_WARM_HI_PORT=$port MAG_WARM_HI_BUDGET_S=3 bash "$script" >/dev/null 2>"$tmp/err"
rc=$?
set -e
[[ "$rc" -ne 0 ]]
grep -q 'Not restarting mag-llm' "$tmp/err"
echo "12-token stub rejected"
kill "$pid"; wait "$pid" 2>/dev/null || true

# The port check can be green while the room hi is still 14–39s. Time the
# first assistant text. A fast room passes; a room that answers at 6s fails.
room_port=$(free_port)
cat >"$tmp/room.py" <<'PY'
import json, sys, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
delay = float(sys.argv[2])
posted = {"t": None}
ThreadingHTTPServer.allow_reuse_address = True
agents = [{"id": "bot-a"}, {"id": "bot-b"}]
class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass
    def _send(self, code, body, kind="application/json"):
        raw = body if isinstance(body, bytes) else body.encode()
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)
    def do_GET(self):
        if self.path.split("?", 1)[0] == "/":
            self._send(200, '<html><head><meta name="mag-token" content="tok123"></head></html>', "text/html")
            return
        if self.path.startswith("/api/state"):
            msgs = []
            if posted["t"] is not None and time.monotonic() - posted["t"] >= delay:
                msgs = [
                    {"id": "u1", "role": "user", "content": "hi"},
                    {"id": "a1", "role": "assistant", "bot": "bot-a", "content": "Hallo"},
                    {"id": "a2", "role": "assistant", "bot": "bot-b", "content": "Hi"},
                ]
            self._send(200, json.dumps({"prefix_ready": True, "agents": agents, "room": msgs}))
            return
        self._send(404, "{}")
    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(n)
        if self.headers.get("X-Mag-Token") != "tok123":
            self._send(403, "{}")
            return
        posted["t"] = time.monotonic()
        self._send(202, '{"accepted":true}')
ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
PY

python3 "$tmp/srv.py" warm "$port" &
pid=$!
python3 "$tmp/room.py" "$room_port" 0 &
pid2=$!
sleep 0.2
start=$(date +%s%3N)
MAG_WARM_HI_PORT=$port MAG_WARM_HI_BUDGET_S=3 MAG_WARM_HI_REQUIRE_ROOM=1 \
  MAG_WARM_HI_ROOM="http://127.0.0.1:$room_port" MAG_WARM_HI_PREWARM_S=2 \
  bash "$script" >"$tmp/out"
ms=$(( $(date +%s%3N) - start ))
grep -q 'visible bot-a' "$tmp/out"
grep -q 'bot-b' "$tmp/out"
[[ "$ms" -lt 5000 ]]
echo "room hi visible ${ms}ms"
kill "$pid" "$pid2"; wait "$pid" "$pid2" 2>/dev/null || true
pid=; pid2=

python3 "$tmp/srv.py" warm "$port" &
pid=$!
python3 "$tmp/room.py" "$room_port" 6 &
pid2=$!
sleep 0.2
start=$(date +%s%3N)
set +e
MAG_WARM_HI_PORT=$port MAG_WARM_HI_BUDGET_S=3 MAG_WARM_HI_REQUIRE_ROOM=1 \
  MAG_WARM_HI_ROOM="http://127.0.0.1:$room_port" MAG_WARM_HI_PREWARM_S=2 \
  MAG_WARM_HI_ANSWER_MS=5000 bash "$script" >/dev/null 2>"$tmp/err"
rc=$?
set -e
ms=$(( $(date +%s%3N) - start ))
[[ "$rc" -ne 0 && "$ms" -lt 12000 && "$ms" -ge 4000 ]]
grep -q 'Not restarting mag-llm' "$tmp/err"
grep -q 'no visible answer' "$tmp/err"
echo "slow room hi rejected ${ms}ms"
kill "$pid" "$pid2"; wait "$pid" "$pid2" 2>/dev/null || true
pid=; pid2=

# ik-llama /slots is state=0 with no n_* fields. A non-empty prefix file plus
# one timed token passes. The same body with no prefix file stays cold.
# A slot still prefilling (Qwen n_prompt_tokens=4096) is cancelled and restored
# before the measurement. A slot that goes idle on its own is not cancelled.
write_prefix() {
  mkdir -p "$tmp/slots/ds-node0" "$tmp/state"
  printf 'prefix-bytes' > "$tmp/slots/ds-node0/mag-prefix.bin"
  python3 -c 'import json,sys; json.dump({sys.argv[1]+"/mag-prefix.bin":"model:abc"}, open(sys.argv[2],"w"))' \
    "$1" "$tmp/state/prompt-prefix.json"
}
rm -rf "$tmp/slots" "$tmp/state"
write_prefix "$port"
: >"$tmp/llama.log"
python3 "$tmp/srv.py" ik "$port" "$tmp/llama.log" &
pid=$!
sleep 0.2
MAG_WARM_HI_PORT=$port MAG_WARM_HI_BUDGET_S=3 MAG_ROOM_STATE="$tmp/state" \
  MAG_WARM_HI_SLOTS="$tmp/slots" bash "$script" >"$tmp/out"
grep -q 'first token' "$tmp/out"
grep -q 'POST /v1/chat/completions' "$tmp/llama.log"
echo "ik slot without n_* fields passes via prefix file and first token"
kill "$pid"; wait "$pid" 2>/dev/null || true

: >"$tmp/llama.log"
python3 "$tmp/srv.py" ik-slow "$port" "$tmp/llama.log" &
pid=$!
sleep 0.2
set +e
MAG_WARM_HI_PORT=$port MAG_WARM_HI_BUDGET_S=3 MAG_WARM_HI_ANSWER_MS=5000 \
  MAG_ROOM_STATE="$tmp/state" MAG_WARM_HI_SLOTS="$tmp/slots" \
  bash "$script" >/dev/null 2>"$tmp/err"
rc=$?
set -e
[[ "$rc" -ne 0 ]]
grep -q 'Not restarting mag-llm' "$tmp/err"
echo "slow ik token rejected"
kill "$pid"; wait "$pid" 2>/dev/null || true

# Production sets REQUIRE_ROOM. The room hi is the measurement, so the gate
# must not send its own completion into the restored KV.
: >"$tmp/llama.log"
python3 "$tmp/srv.py" ik "$port" "$tmp/llama.log" &
pid=$!
python3 "$tmp/room.py" "$room_port" 0 &
pid2=$!
sleep 0.2
MAG_WARM_HI_PORT=$port MAG_WARM_HI_BUDGET_S=3 MAG_WARM_HI_REQUIRE_ROOM=1 \
  MAG_WARM_HI_ROOM="http://127.0.0.1:$room_port" MAG_WARM_HI_PREWARM_S=2 \
  MAG_ROOM_STATE="$tmp/state" MAG_WARM_HI_SLOTS="$tmp/slots" \
  bash "$script" >"$tmp/out"
grep -q 'prefix file' "$tmp/out"
grep -q 'visible bot-a' "$tmp/out"
! grep -q 'POST /v1/chat/completions' "$tmp/llama.log"
echo "ik prefix file leaves the room hi as the measurement"
kill "$pid" "$pid2"; wait "$pid" "$pid2" 2>/dev/null || true
pid=; pid2=

rm -f "$tmp/state/prompt-prefix.json"
: >"$tmp/llama.log"
python3 "$tmp/srv.py" ik "$port" "$tmp/llama.log" &
pid=$!
python3 "$tmp/room.py" "$room_port" 0 &
pid2=$!
sleep 0.2
MAG_WARM_HI_PORT=$port MAG_WARM_HI_BUDGET_S=3 MAG_WARM_HI_REQUIRE_ROOM=1 \
  MAG_WARM_HI_ROOM="http://127.0.0.1:$room_port" MAG_WARM_HI_PREWARM_S=2 \
  MAG_ROOM_STATE="$tmp/state" MAG_WARM_HI_SLOTS="$tmp/slots" \
  bash "$script" >"$tmp/out"
grep -q 'prefix unchecked' "$tmp/out"
grep -q 'visible bot-a' "$tmp/out"
! grep -q 'POST /v1/chat/completions' "$tmp/llama.log"
echo "ik without a prefix file is decided by the room hi"
kill "$pid" "$pid2"; wait "$pid" "$pid2" 2>/dev/null || true
pid=; pid2=

write_prefix "$port"
: >"$tmp/llama.log"
python3 "$tmp/srv.py" prefill "$port" "$tmp/llama.log" &
pid=$!
sleep 0.2
start=$(date +%s%3N)
MAG_WARM_HI_PORT=$port MAG_WARM_HI_BUDGET_S=3 MAG_WARM_HI_SETTLE_S=0.3 \
  MAG_ROOM_STATE="$tmp/state" MAG_WARM_HI_SLOTS="$tmp/slots" \
  bash "$script" >"$tmp/out"
ms=$(( $(date +%s%3N) - start ))
grep -q 'prefix resident' "$tmp/out"
grep -q 'POST /slots/0?action=cancel' "$tmp/llama.log"
grep -q 'POST /slots/0?action=restore' "$tmp/llama.log"
! grep -q 'POST /v1/chat/completions' "$tmp/llama.log"
echo "qwen prefill cancelled and restored ${ms}ms"
kill "$pid"; wait "$pid" 2>/dev/null || true

: >"$tmp/llama.log"
python3 "$tmp/srv.py" prefill-wait "$port" "$tmp/llama.log" &
pid=$!
sleep 0.2
MAG_WARM_HI_PORT=$port MAG_WARM_HI_BUDGET_S=3 MAG_WARM_HI_SETTLE_S=2 \
  MAG_ROOM_STATE="$tmp/state" MAG_WARM_HI_SLOTS="$tmp/slots" \
  bash "$script" >"$tmp/out"
grep -q 'prefix resident' "$tmp/out"
! grep -q 'action=cancel' "$tmp/llama.log"
echo "qwen prefill that finishes is not cancelled"
kill "$pid"; wait "$pid" 2>/dev/null || true

: >"$tmp/llama.log"
python3 "$tmp/srv.py" prefill-ik "$port" "$tmp/llama.log" &
pid=$!
sleep 0.2
MAG_WARM_HI_PORT=$port MAG_WARM_HI_BUDGET_S=3 MAG_WARM_HI_SETTLE_S=0.3 \
  MAG_ROOM_STATE="$tmp/state" MAG_WARM_HI_SLOTS="$tmp/slots" \
  bash "$script" >"$tmp/out"
grep -q 'first token' "$tmp/out"
grep -q 'POST /slots/0?action=cancel' "$tmp/llama.log"
grep -q 'POST /slots/0?action=restore' "$tmp/llama.log"
grep -q 'POST /v1/chat/completions' "$tmp/llama.log"
echo "prefill that restores to an ik slot is measured"
kill "$pid"; wait "$pid" 2>/dev/null || true
echo "warm hi gate PASS"
