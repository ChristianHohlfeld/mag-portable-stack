#!/usr/bin/env bash
# Diagnose GLM-5.3 chat output (empty content with enable_thinking=false).
# Run as chris on mag:  bash ops/glm/diag-chat.sh [--template-only]
#
# Stage A (no service touched): print the GGUF's embedded chat template and
#   whether it honours enable_thinking.
# Stage B (temporary, bounded): stop Qwen, run GLM as the transient unit
#   mag-glm-diag on 127.0.0.1:8193 (not :8192, so the room never talks to it),
#   print rendered prompts and raw/parsed answers, then ALWAYS stop GLM and
#   restart Qwen (EXIT/INT/TERM trap) and run the Qwen quick acceptance.
# Only a diagnostic log is written; the transient unit and Qwen state are restored.
set -euo pipefail
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
MODEL=/home/chris/models/GLM-5.3-Flash-GGUF-IQ1_M/GLM-5.3-Flash-IQ1_M-00001-of-00015.gguf
REV=$(cat "$ROOT/ops/qwen/runtime.sha")
BIN="/home/chris/.local/qwen4exp/$REV-mag-cp1/bin/llama-server"
LOG="$HOME/glm-diag-$(date +%Y%m%d-%H%M).log"
[[ $(id -un) == chris ]] || { echo 'run as chris (sudo is used where needed)' >&2; exit 4; }
[[ -x $BIN && -f $MODEL && -f "$ROOT/ops/glm/chat-template.jinja" ]] || { echo "missing runtime, GGUF or pinned template" >&2; exit 4; }
exec > >(tee -a "$LOG") 2>&1
echo "log: $LOG"

echo '== A: embedded chat template'
python3 -I "$ROOT/ops/glm/inspect-gguf.py" "$MODEL" --dump-chat-template --require-thinking-switch || true
[[ ${1:-} == --template-only ]] && exit 0

echo '== B: temporary GLM on :8193 (Qwen restored afterwards)'
for u in mag-backup-daily mag-backup-weekly mag-backup-monthly mag-backup-restore-test mag-backup-deploy; do
  if systemctl is-active --quiet "$u.service"; then echo "backup running ($u); try later" >&2; exit 4; fi
done
api(){ curl --noproxy '*' -sS --max-time 1800 -H 'Content-Type: application/json' "http://127.0.0.1:8193$1" -d "$2"; }
wait_health(){ # port timeout_s
  local i
  for ((i=0; i<$2/5; i++)); do
    curl --noproxy '*' -fsS --max-time 3 -o /dev/null "http://127.0.0.1:$1/health" && return 0
    sleep 5
  done
  return 1
}
QWEN_WAS_ACTIVE=0
systemctl is-active --quiet mag-llm-qwen.service && QWEN_WAS_ACTIVE=1
restore(){
  trap - EXIT INT TERM
  echo '== restore'
  sudo -n systemctl stop mag-glm-diag.service 2>/dev/null || true
  if (( QWEN_WAS_ACTIVE )); then
    sudo -n systemctl start mag-llm-qwen.service
    if wait_health 8192 5400; then
      python3 -I "$ROOT/ops/qwen/smoke.py" --port 8192 --quick --model qwen3.8-flash-next-uncensored \
        && echo 'Qwen restored and accepted' || echo 'WARNING: Qwen restored but acceptance failed' >&2
    else
      echo 'WARNING: Qwen health timeout after restore' >&2
    fi
  fi
}
trap restore EXIT INT TERM

sudo -n systemctl stop mag-llm-qwen.service
sudo -n systemd-run --unit=mag-glm-diag --collect --uid=chris --gid=chris -p LimitMEMLOCK=infinity \
  /usr/bin/numactl --physcpubind=14-27 --membind=1 "$BIN" \
  --model "$MODEL" --alias glm-5.3-flash --host 127.0.0.1 --port 8193 \
  --ctx-size 8192 --parallel 1 --threads 14 --threads-batch 14 --numa numactl \
  --load-mode dio --jinja --chat-template-file "$ROOT/ops/glm/chat-template.jinja" --reasoning-format auto --no-ui
echo 'loading GLM (~3 min) ...'
for ((i=0; i<360; i++)); do
  systemctl is-active --quiet mag-glm-diag.service || { echo 'GLM exited during load:'; sudo -n journalctl -u mag-glm-diag --no-pager | tail -40; exit 4; }
  wait_health 8193 5 && break
done

MSG='[{"role":"user","content":"Reply READY."}]'
for t in false true; do
  echo "== rendered prompt, enable_thinking=$t (tail)"
  api /apply-template "{\"messages\":$MSG,\"chat_template_kwargs\":{\"enable_thinking\":$t}}" \
    | python3 -c 'import json,sys; p=json.load(sys.stdin).get("prompt",""); print(repr(p[-300:]))'
done
show='import json,sys
a=json.load(sys.stdin); c=(a.get("choices") or [{}])[0]; m=c.get("message") or {}
print("finish_reason:",c.get("finish_reason")); print("usage:",a.get("usage")); print("timings:",a.get("timings"))
print("content:",repr(m.get("content"))); print("reasoning_content:",repr(m.get("reasoning_content")))'
echo '== smoke request (parsed, reasoning-format auto)'
api /v1/chat/completions "{\"model\":\"glm-5.3-flash\",\"messages\":$MSG,\"max_tokens\":192,\"temperature\":0,\"chat_template_kwargs\":{\"enable_thinking\":false}}" | python3 -c "$show"
echo '== same request, raw text (reasoning_format none)'
api /v1/chat/completions "{\"model\":\"glm-5.3-flash\",\"messages\":$MSG,\"max_tokens\":192,\"temperature\":0,\"reasoning_format\":\"none\",\"chat_template_kwargs\":{\"enable_thinking\":false}}" | python3 -c "$show"
echo '== server log (template/format lines)'
sudo -n journalctl -u mag-glm-diag --no-pager | grep -iE 'chat|template|think|reason|format|elementwise|warn|error' | tail -40
