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
    warm = False
    for key in ("n_past", "n_prompt_tokens", "n_cached_tokens"):
        if isinstance(slot.get(key), (int, float)) and slot[key] > 0:
            warm = True
    nxt = slot.get("next_token")
    if isinstance(nxt, dict):
        n = nxt.get("n_decoded")
        if isinstance(n, (int, float)) and n > 0:
            warm = True
    prompt = slot.get("prompt")
    if isinstance(prompt, str) and prompt:
        warm = True
    if isinstance(prompt, list) and prompt:
        warm = True
    state = slot.get("state")
    if isinstance(state, (int, float)) and state > 0:
        warm = True
    if not warm:
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
echo "warm hi gate: ${#port_list[@]} bot(s) ready for a first hi under $((limit_ms / 1000))s"
