#!/usr/bin/env bash
# GLM-5.3-Flash (llama.cpp arch glm5-next) on NUMA1 only, port 8192.
# Same pinned upstream llama.cpp runtime as Qwen (ops/qwen/runtime.sha; the
# commit has src/models/glm5-next.cpp). 14 physical cores of socket 1, every
# weight page allocated on node1:
# --load-mode mlock reads into anonymous memory (see ops/qwen/start.sh: mmap
# pages left in the page cache by an earlier read cannot be moved by --membind).
# The model is hybrid (KDA linear layers + MLA), so prompt reuse needs context
# checkpoints, exactly like the hybrid Qwen.
set -euo pipefail
fail(){ echo "GLM NUMA1: $*" >&2; exit 4; }
REV=$(cat /usr/local/lib/mag-glm/runtime.sha)
[[ $REV =~ ^[a-f0-9]{40}$ ]] || fail 'runtime sha invalid'
BIN="/home/chris/.local/qwen4exp/$REV-mag-cp1/bin/llama-server"
MODEL="/home/chris/models/GLM-5.3-Flash-GGUF-IQ1_M/GLM-5.3-Flash-IQ1_M-00001-of-00015.gguf"
[[ -x "$BIN" && -f "$MODEL" ]] || fail 'runtime / GGUF missing'
[[ $(cat "/home/chris/.local/qwen4exp/$REV-mag-cp1/bin/source-commit") == "$REV" ]] || fail 'runtime version not pinned'
[[ $(cat /sys/devices/system/node/node1/cpulist) == "14-27,42-55" ]] || fail 'unexpected NUMA1 topology'
systemctl is-active --quiet mag-llm-iq3.service && fail 'IQ3 still running; explicit operator stop required'
systemctl is-active --quiet mag-llm-qwen.service && fail 'Qwen still running on NUMA1'
for u in mag-backup-daily mag-backup-weekly mag-backup-monthly mag-backup-restore-test mag-backup-deploy mag-backup-clear; do
  systemctl is-active --quiet "$u.service" && fail "backup active: $u"
done
# 66 GB weights + 32k KV/recurrent state + 16 GiB prompt RAM cache, plus reserve.
read -r free file shmem < <(awk '$3=="MemFree:"{f=$4} $3=="FilePages:"{p=$4} $3=="Shmem:"{s=$4} END{print f+0,p+0,s+0}' /sys/devices/system/node/node1/meminfo)
usable=$((free + (file > shmem ? file-shmem : 0)))
(( usable >= 110*1024*1024 )) || fail 'NUMA1 reclaimable RAM below 110GiB (reserve for system/backup)'
bash /usr/local/lib/mag-glm/verify-model.sh || fail 'model verification failed'
exec /usr/bin/numactl --physcpubind=14-27 --membind=1 "$BIN" \
  --model "$MODEL" --alias glm-5.3-flash \
  --host 127.0.0.1 --port "${GLM_PORT:-8192}" --ctx-size 32768 --parallel 1 \
  --threads 14 --threads-batch 14 --numa numactl \
  --load-mode mlock \
  --jinja --reasoning-format auto --cache-ram 16384 --ctx-checkpoints 8 --checkpoint-min-step 128 \
  --slots --slot-save-path /var/lib/mag-llm/slots/glm --no-ui
