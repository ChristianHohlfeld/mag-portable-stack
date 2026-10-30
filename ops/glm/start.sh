#!/usr/bin/env bash
# One pinned GLM5-Next runtime for both NUMA nodes; per-instance model, port,
# CPUs and slot directories are the only hardware/model variations.
set -euo pipefail
instance=${1:?usage: start.sh q1|q2}
[[ $instance == q1 || $instance == q2 ]] || exit 4
source "/etc/mag-glm/$instance.env"
fail(){ echo "GLM $instance: $*" >&2; exit 4; }
REV=$(cat /usr/local/lib/mag-glm/runtime.sha)
[[ $REV =~ ^[a-f0-9]{40}$ ]] || fail 'runtime sha invalid'
BIN="/home/chris/.local/qwen4exp/$REV-mag-cp1/bin/llama-server"
TEMPLATE=/usr/local/lib/mag-glm/chat-template.jinja
[[ -x $BIN && -f $TEMPLATE ]] || fail 'runtime/template missing'
[[ $(cat "$(dirname "$BIN")/source-commit" 2>/dev/null) == "$REV" ]] || fail 'runtime version not pinned'
[[ $GLM_NODE =~ ^[01]$ && $GLM_PORT =~ ^819[12]$ ]] || fail 'bad node/port'
[[ ( $instance == q1 && $GLM_NODE == 0 && $GLM_PORT == 8191 ) ||
   ( $instance == q2 && $GLM_NODE == 1 && $GLM_PORT == 8192 ) ]] || fail 'instance routing mismatch'
# Expand shard-1 from a literal GGUF or glob; never choose a later split shard.
shopt -s nullglob
candidates=( $GLM_MODEL )
(( ${#candidates[@]} > 0 )) || fail "GGUF missing: $GLM_MODEL"
MODEL=${candidates[0]}
[[ -f $MODEL && $(head -c 4 "$MODEL") == GGUF ]] || fail 'invalid GGUF first shard'
[[ -d $GLM_SLOT_DIR && ! -L $GLM_SLOT_DIR ]] || fail 'slot directory missing'
[[ $(cat "/sys/devices/system/node/node$GLM_NODE/cpulist") == "$GLM_CPU_TOPOLOGY" ]] || fail 'unexpected NUMA topology'
for u in mag-backup-daily mag-backup-weekly mag-backup-monthly mag-backup-restore-test mag-backup-deploy mag-backup-clear; do
  systemctl is-active --quiet "$u.service" && fail "backup active: $u"
done
# Keep Q2's exact #231 format and slot directory; Q1 gets a distinct tag/path.
if [[ $instance == q1 ]]; then
  FORMAT='glm-5.3-flash-iq1-m:kv-f16-f16:ctx32768:slot0'
  legacy="mag-prefix-glm-5.3-flash-iq1-m-slot1.bin"
  binding=(--physcpubind=0-13 --membind=0)
else
  FORMAT='glm-5.3-flash:kv-f16-f16:ctx32768:slot0'
  legacy="mag-prefix-glm-5.3-flash-slot2.bin"
  binding=(--physcpubind=14-27 --membind=1)
  # Original NUMA1 IQ2_XS memory gate from #231.
  read -r free file shmem < <(awk '$3=="MemFree:"{f=$4} $3=="FilePages:"{p=$4} $3=="Shmem:"{s=$4} END{print f+0,p+0,s+0}' /sys/devices/system/node/node1/meminfo)
  usable=$((free + (file > shmem ? file-shmem : 0)))
  (( usable >= 145*1024*1024 )) || fail 'NUMA1 reclaimable RAM below 145GiB'
  bash /usr/local/lib/mag-glm/verify-model.sh || fail 'IQ2 model verification failed'
fi
if [[ ! -f "$GLM_SLOT_DIR/.kv-format" || $(cat "$GLM_SLOT_DIR/.kv-format") != "$FORMAT" ]]; then
  rm -f -- "$GLM_SLOT_DIR/$legacy"
  printf '%s\n' "$FORMAT" > "$GLM_SLOT_DIR/.kv-format.tmp"
  mv -f -- "$GLM_SLOT_DIR/.kv-format.tmp" "$GLM_SLOT_DIR/.kv-format"
fi
# Broadwell: 14 physical cores per socket, 14 SMT siblings deliberately
# excluded from both inference and prefill. This is a hardware-derived
# low-contention profile, NOT a benchmark-proven fastest thread count.
# For another profile, compare normal /props or request timings before tuning.
# llama id_slot=0 on both servers (--parallel 1); room slot 1/2 is a
# different identity in the durable checkpoint filename.
exec /usr/bin/numactl "${binding[@]}" "$BIN" \
  --model "$MODEL" --alias "$GLM_ALIAS" \
  --host 127.0.0.1 --port "$GLM_PORT" --ctx-size 32768 --parallel 1 \
  --threads 14 --threads-batch 14 --numa numactl \
  --load-mode dio -fa on -b 4096 -ub 1024 \
  --jinja --chat-template-file "$TEMPLATE" --reasoning-format auto \
  --temp 1.0 --top-p 0.95 --cache-ram 16384 --ctx-checkpoints 64 --checkpoint-min-step 1024 \
  --slots --slot-save-path "$GLM_SLOT_DIR" --no-ui
