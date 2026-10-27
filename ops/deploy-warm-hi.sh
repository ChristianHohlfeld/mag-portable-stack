#!/usr/bin/env bash
# After mag-120 is up: both NUMA slots must already hold a prefix that a
# first hi can extend in under 5s. The slots document is large; pass it on
# stdin, never as an argument (Argument list too long). This check does not
# send a chat completion (that would replace the cached prefix) and does not
# restart mag-llm-ds-node@0.
set -euo pipefail
budget=${MAG_WARM_HI_BUDGET_S:-5}
limit_ms=${MAG_WARM_HI_ANSWER_MS:-5000}
if [[ -n "${MAG_WARM_HI_PORT:-}" ]]; then
  ports=$MAG_WARM_HI_PORT
else
  ports=${MAG_WARM_HI_PORTS:-8191,8192}
fi
IFS=',' read -r -a port_list <<<"$ports"
fail() { echo "FAIL: $*" >&2; echo "Not restarting mag-llm." >&2; exit 1; }

check_port() {
  local port=$1
  local start end ms code slots
  start=$(date +%s%3N)
  code=$(curl -sS -o /dev/null -w '%{http_code}' --retry 0 --max-time "$budget" \
    "http://127.0.0.1:${port}/health" || true)
  end=$(date +%s%3N)
  ms=$((end - start))
  if [[ "$code" != 200 || "$ms" -ge "$limit_ms" ]]; then
    fail "warm hi gate: :${port}/health HTTP '${code:-none}' in ${ms}ms (need <${limit_ms}ms)"
  fi
  slots=$(curl -sS --retry 0 --max-time "$budget" "http://127.0.0.1:${port}/slots" || true)
  if [[ -z "$slots" ]]; then
    fail "warm hi gate: :${port}/slots silent in ${budget}s"
  fi
  printf '%s' "$slots" | python3 -c '
import json, sys
raw = sys.stdin.read()
try:
    slots = json.loads(raw)
except Exception:
    sys.exit(2)
if isinstance(slots, dict):
    slots = slots.get("slots") or slots.get("data") or []
if not isinstance(slots, list) or not slots:
    sys.exit(3)
for slot in slots:
    if slot.get("is_processing"):
        sys.exit(4)
    # n_decoded, a prompt string or state>0 is not the room prefix
    # (live: n_past=1 / 12 tokens still printed "prefix resident").
    count = 0
    for key in ("n_past", "n_prompt_tokens", "n_cached_tokens"):
        n = slot.get(key)
        if isinstance(n, (int, float)) and n > count:
            count = n
    if count < 2048:
        sys.exit(5)
' || {
    fail "warm hi gate: :${port} slot is busy or has no cached prefix (health ${ms}ms)"
  }
  echo "warm hi gate: :${port}/health ${ms}ms, prefix resident"
}

for port in "${port_list[@]}"; do
  port=${port// /}
  [[ -n "$port" ]] || continue
  check_port "$port"
done

# Health on :8191/:8192 only proves the slots are idle. The room hi is what
# the deploy measured at 14–39s after a green gate. Time the first visible
# assistant token, not the completed turn.
if [[ "${MAG_WARM_HI_REQUIRE_ROOM:-0}" == 1 ]]; then
  room=${MAG_WARM_HI_ROOM:-http://127.0.0.1:8766}
  prewarm_s=${MAG_WARM_HI_PREWARM_S:-60}
  python3 - "$room" "$limit_ms" "$prewarm_s" <<'PY'
import json, sys, time, urllib.request
room, limit_ms, prewarm_s = sys.argv[1].rstrip("/"), int(sys.argv[2]), float(sys.argv[3])

def get(path, timeout):
    with urllib.request.urlopen(room + path, timeout=timeout) as r:
        return r.status, r.read()

deadline = time.monotonic() + prewarm_s
ready = False
while time.monotonic() < deadline:
    try:
        status, body = get("/api/state", 2)
    except Exception:
        time.sleep(0.2)
        continue
    if status != 200:
        time.sleep(0.2)
        continue
    state = json.loads(body)
    if state.get("prefix_ready", True):
        ready = True
        break
    time.sleep(0.2)
if not ready:
    sys.stderr.write("FAIL: warm hi gate: prefix not ready\nNot restarting mag-llm.\n")
    sys.exit(1)
status, html = get("/", 3)
token = ""
text = html.decode("utf-8", "replace")
key = 'name="mag-token" content="'
at = text.find(key)
if at >= 0:
    token = text[at + len(key):].split('"', 1)[0]
if not token:
    sys.stderr.write("FAIL: warm hi gate: no room token\nNot restarting mag-llm.\n")
    sys.exit(1)
state = json.loads(get("/api/state", 3)[1])
bots = [a.get("id") for a in state.get("agents") or [] if a.get("id")]
if len(bots) < 1:
    sys.stderr.write("FAIL: warm hi gate: no bots\nNot restarting mag-llm.\n")
    sys.exit(1)
before = {m.get("id") for m in state.get("room") or []}
payload = json.dumps({"text": "hi", "bots": bots}).encode()
req = urllib.request.Request(room + "/api/message", data=payload, method="POST",
    headers={"Content-Type": "application/json", "X-Mag-Token": token})
started = time.monotonic()
with urllib.request.urlopen(req, timeout=5) as r:
    if r.status not in (200, 202):
        sys.stderr.write("FAIL: warm hi gate: message HTTP %s\nNot restarting mag-llm.\n" % r.status)
        sys.exit(1)
need = set(bots)
seen = {}
while time.monotonic() - started < limit_ms / 1000:
    state = json.loads(get("/api/state", 2)[1])
    for m in state.get("room") or []:
        if m.get("id") in before or m.get("role") != "assistant":
            continue
        bot = m.get("bot")
        if bot in need and (m.get("content") or "").strip() and bot not in seen:
            seen[bot] = int((time.monotonic() - started) * 1000)
    if need <= set(seen):
        break
    time.sleep(0.05)
missing = [b for b in bots if b not in seen]
if missing:
    sys.stderr.write("FAIL: warm hi gate: no visible answer for %s within %sms\nNot restarting mag-llm.\n" % (",".join(missing), limit_ms))
    sys.exit(1)
slow = [b for b, ms in seen.items() if ms >= limit_ms]
if slow:
    sys.stderr.write("FAIL: warm hi gate: %s\nNot restarting mag-llm.\n" % " ".join("%s %sms" % (b, seen[b]) for b in slow))
    sys.exit(1)
print("warm hi gate: visible " + " ".join("%s %sms" % (b, seen[b]) for b in bots))
PY
fi
echo "warm hi gate: ${#port_list[@]} bot(s) ready for a first hi under $((limit_ms / 1000))s"
