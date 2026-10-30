#!/usr/bin/env bash
# GLM-5.3-Flash (llama.cpp arch glm5-next) on NUMA1 only, port 8192.
# Same pinned upstream llama.cpp runtime as Qwen (ops/qwen/runtime.sha; the
# commit has src/models/glm5-next.cpp). 14 physical cores of socket 1, every
# weight page allocated on node1:
# --load-mode dio reads the GGUF with O_DIRECT into anonymous memory on node1:
# no page-cache copy (mmap pages cached on node0 by an earlier read could not be
# moved by --membind, and "mlock" doubled the footprint as page cache + locked
# copy, which failed with ENOMEM). Swap is off for the unit (MemorySwapMax=0),
# so locking adds nothing.
# The model is hybrid (KDA linear layers + MLA), so prompt reuse needs context
# checkpoints, exactly like the hybrid Qwen.
set -euo pipefail
fail(){ echo "GLM NUMA1: $*" >&2; exit 4; }
REV=$(cat /usr/local/lib/mag-glm/runtime.sha)
[[ $REV =~ ^[a-f0-9]{40}$ ]] || fail 'runtime sha invalid'
BIN="/home/chris/.local/qwen4exp/$REV-mag-cp1/bin/llama-server"
MODEL="/home/chris/models/GLM-5.3-Flash-GGUF-IQ2_XS/GLM-5.3-Flash-IQ2_XS-00001-of-00015.gguf"
TEMPLATE=/usr/local/lib/mag-glm/chat-template.jinja
[[ -x "$BIN" && -f "$MODEL" && -f "$TEMPLATE" ]] || fail 'runtime / GGUF / chat template missing'
[[ $(cat "/home/chris/.local/qwen4exp/$REV-mag-cp1/bin/source-commit") == "$REV" ]] || fail 'runtime version not pinned'
[[ $(cat /sys/devices/system/node/node1/cpulist) == "14-27,42-55" ]] || fail 'unexpected NUMA1 topology'
systemctl is-active --quiet mag-llm-iq3.service && fail 'IQ3 still running; explicit operator stop required'
systemctl is-active --quiet mag-llm-qwen.service && fail 'Qwen still running on NUMA1'
for u in mag-backup-daily mag-backup-weekly mag-backup-monthly mag-backup-restore-test mag-backup-deploy mag-backup-clear; do
  systemctl is-active --quiet "$u.service" && fail "backup active: $u"
done
# 85.8 GiB IQ2_XS weights + 32k KV/recurrent state + 16 GiB prompt RAM cache, plus reserve.
read -r free file shmem < <(awk '$3=="MemFree:"{f=$4} $3=="FilePages:"{p=$4} $3=="Shmem:"{s=$4} END{print f+0,p+0,s+0}' /sys/devices/system/node/node1/meminfo)
usable=$((free + (file > shmem ? file-shmem : 0)))
(( usable >= 145*1024*1024 )) || fail 'NUMA1 reclaimable RAM below 145GiB (reserve for system/backup)'
bash /usr/local/lib/mag-glm/verify-model.sh || fail 'model verification failed'
# One-time KV format migration. An old f16 slot cannot be restored with
# q8_0 K/V. Store a durable format tag; on format changes invalidate only
# the GLM prefix (the room metadata is corrected on failed restore).
SLOTDIR=/var/lib/mag-llm/slots/glm
FORMAT='glm-5.3-flash:kv-q8_0-q8_0:ctx32768:slot0'
if [[ ! -f "$SLOTDIR/.kv-format" || $(cat "$SLOTDIR/.kv-format") != "$FORMAT" ]]; then
  rm -f -- "$SLOTDIR/mag-prefix-glm-5.3-flash-slot2.bin"
  printf '%s\\n' "$FORMAT" > "$SLOTDIR/.kv-format.tmp"
  mv -f -- "$SLOTDIR/.kv-format.tmp" "$SLOTDIR/.kv-format"
fi
# slot0 is the llama id_slot: parallel=1; slot2 is the durable room
# backend identity in the checkpoint filename, not a llama slot index.
exec /usr/bin/numactl --physcpubind=14-27,42-55 --membind=1 "$BIN" \
  --model "$MODEL" --alias glm-5.3-flash \
  --host 127.0.0.1 --port "${GLM_PORT:-8192}" --ctx-size 32768 --parallel 1 \
  --threads 14 --threads-batch 28 --numa numactl \
  --load-mode dio -fa on -b 2048 -ub 1024 -ctk q8_0 -ctv q8_0 \
  --cache-reuse 256 \
  --jinja --chat-template-file "$TEMPLATE" --reasoning-format auto --cache-ram 16384 --ctx-checkpoints 8 --checkpoint-min-step 128 \
  --slots --slot-save-path /var/lib/mag-llm/slots/glm --no-ui
