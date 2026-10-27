#!/usr/bin/env bash
# After mag-120 is up: a warm DeepSeek slot must answer /health in under 10s.
# ik_llama serves /health on the same task queue as prefill, so a restart that
# re-prefills the only slot makes this fail. The check does not send a chat
# completion (that would replace the cached prefix) and does not restart
# mag-llm-ds-node@0.
set -euo pipefail
port=${MAG_WARM_HI_PORT:-8191}
budget=${MAG_WARM_HI_BUDGET_S:-8}
start=$(date +%s%3N)
code=$(curl -sS -o /dev/null -w '%{http_code}' --retry 0 --max-time "$budget" \
  "http://127.0.0.1:${port}/health" || true)
end=$(date +%s%3N)
ms=$((end - start))
if [[ "$code" != 200 || "$ms" -ge 10000 ]]; then
  echo "FAIL: warm hi gate: :${port}/health HTTP '${code:-none}' in ${ms}ms (budget ${budget}s). Not restarting mag-llm." >&2
  exit 1
fi
slots=$(curl -fsS --retry 0 --max-time "$budget" "http://127.0.0.1:${port}/slots" || true)
python3 -c '
import json, sys
raw = sys.argv[1]
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
' "$slots" || {
  echo "FAIL: warm hi gate: :${port} slot is busy or has no cached prefix (health ${ms}ms). Not restarting mag-llm." >&2
  exit 1
}
echo "warm hi gate: :${port}/health ${ms}ms, prefix resident"
