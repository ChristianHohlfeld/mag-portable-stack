#!/usr/bin/env bash
# Parameterized Qwen3.8 CPU launcher. The standalone fallback remains NUMA1;
# the MAG GLM room uses NUMA0 (:8191) beside the untouched GLM-Q2 on NUMA1.
set -euo pipefail
fail(){ echo "Qwen NUMA${QWEN_NODE:-?}: $*" >&2; exit 4; }
REV=$(cat /usr/local/lib/mag-qwen/runtime.sha)
BIN="/home/chris/.local/qwen4exp/$REV-mag-cp1/bin/llama-server"
MODEL="/home/chris/models/qwen3.8-flash-next-uncensored/Qwen3.8-Flash-Next-Uncensored.IQ4_XS.gguf"
NODE=${QWEN_NODE:-1}
case "$NODE" in
  0)
    PORT=${QWEN_PORT:-8191}
    CPU_RANGE='0-13,28-41'
    BIND='0-13,28-41'
    BATCH_THREADS=${QWEN_THREADS_BATCH:-28}
    MIN_RAM_GIB=${QWEN_MIN_RAM_GIB:-150}
    ;;
  1)
    PORT=${QWEN_PORT:-8091}
    CPU_RANGE='14-27,42-55'
    BIND='14-27'
    BATCH_THREADS=${QWEN_THREADS_BATCH:-14}
    MIN_RAM_GIB=${QWEN_MIN_RAM_GIB:-150}
    ;;
  *) fail 'QWEN_NODE must be 0 or 1' ;;
esac
[[ $NODE != 0 || $PORT == 8191 ]] || fail 'NUMA0 room service must use :8191'
[[ $NODE != 1 || $PORT == 8091 || $PORT == 8192 ]] || fail 'NUMA1 port must be :8091 or :8192'
THREADS=${QWEN_THREADS:-14}
[[ $THREADS =~ ^[1-9][0-9]*$ && $BATCH_THREADS =~ ^[1-9][0-9]*$ &&
   $MIN_RAM_GIB =~ ^[1-9][0-9]*$ ]] || fail 'invalid thread/RAM setting'
[[ -x "$BIN" && -f "$MODEL" ]] || fail 'runtime / GGUF missing'
[[ $(cat "/home/chris/.local/qwen4exp/$REV-mag-cp1/bin/source-commit") == "$REV" ]] || fail 'runtime version not pinned'
[[ $(cat "/sys/devices/system/node/node$NODE/cpulist") == "$CPU_RANGE" ]] || fail 'unexpected NUMA topology'
systemctl is-active --quiet mag-llm-iq3.service && fail 'IQ3 still running; explicit operator stop required'
# Q4 is checked as root in systemd ExecStartPre (audit firewall blocks chris on port 8090).
for u in mag-backup-daily mag-backup-weekly mag-backup-monthly mag-backup-restore-test mag-backup-deploy mag-backup-clear; do
  systemctl is-active --quiet "$u.service" && fail "backup active: $u"
done
read -r free file shmem < <(awk '$3=="MemFree:"{f=$4} $3=="FilePages:"{p=$4} $3=="Shmem:"{s=$4} END{print f+0,p+0,s+0}' "/sys/devices/system/node/node$NODE/meminfo")
usable=$((free + (file > shmem ? file-shmem : 0)))
(( usable >= MIN_RAM_GIB*1024*1024 )) || fail "NUMA$NODE reclaimable RAM below ${MIN_RAM_GIB}GiB (reserve for system/backup)"
bash /usr/local/lib/mag-qwen/verify-model.sh || fail 'model SHA verification failed'
exec /usr/bin/numactl --physcpubind="$BIND" --membind="$NODE" "$BIN" \
  --model "$MODEL" --alias qwen3.8-flash-next-uncensored \
  --host 127.0.0.1 --port "$PORT" --ctx-size 32768 --parallel 1 \
  --threads "$THREADS" --threads-batch "$BATCH_THREADS" --numa numactl \
  --load-mode mlock --lazy-mode off -ot per_layer_token_embd=CPU \
  --jinja --reasoning-format auto --cache-ram 16384 --ctx-checkpoints 8 --checkpoint-min-step 128 \
  --slots --slot-save-path /var/lib/mag-llm/slots/qwen --no-ui
