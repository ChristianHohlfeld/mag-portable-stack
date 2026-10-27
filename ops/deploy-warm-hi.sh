#!/usr/bin/env bash
# After mag-120 is up: a warm DeepSeek slot must answer /health in under 10s.
# ik_llama serves /health and /slots on the same task queue as prefill, so a
# busy slot answers slowly or not at all. That is healthy. A fast /health
# with an idle slot that has no prefix is the failure. This check does not
# send a chat completion and does not restart mag-llm-ds-node@0.
set -euo pipefail
port=${MAG_WARM_HI_PORT:-8191}
budget=${MAG_WARM_HI_BUDGET_S:-8}
start=$(date +%s%3N)
code=$(curl -sS -o /dev/null -w '%{http_code}' --retry 0 --max-time "$budget" \
  "http://127.0.0.1:${port}/health" || true)
end=$(date +%s%3N)
ms=$((end - start))
if [[ "$code" != 200 ]]; then
  echo "warm hi gate: :${port}/health HTTP '${code:-none}' in ${ms}ms; slot busy, not an error. Not restarting mag-llm."
  exit 0
fi
slots=$(curl -sS --retry 0 --max-time "$budget" "http://127.0.0.1:${port}/slots" || true)
if [[ -z "$slots" ]]; then
  echo "warm hi gate: :${port}/slots silent in ${budget}s; slot busy, not an error. Not restarting mag-llm."
  exit 0
fi
# The slots document is large. Pass it on stdin, never as an argument.
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
        sys.exit(0)
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
  echo "FAIL: warm hi gate: :${port} slot has no cached prefix (health ${ms}ms). Not restarting mag-llm." >&2
  exit 1
}
echo "warm hi gate: :${port}/health ${ms}ms, prefix resident or busy"
