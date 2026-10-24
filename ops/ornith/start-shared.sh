#!/usr/bin/env bash
# Ornith-1.5-35B-A3B shared server (variant A, ops/ornith/README.md): ONE model copy
# for all four room bots, --parallel 4 (one llama slot per bot, id_slot forced by the
# gateway), 28 physical cores over both NUMA nodes, memory interleaved. Port 8191.
# Same pinned upstream llama.cpp runtime as Qwen (ops/qwen/runtime.sha).
set -euo pipefail
fail(){ echo "Ornith shared: $*" >&2; exit 4; }
REV=$(cat /usr/local/lib/mag-ornith/runtime.sha)
[[ $REV =~ ^[a-f0-9]{40}$ ]] || fail 'runtime sha invalid'
BIN="/home/chris/.local/qwen4exp/$REV/bin/llama-server"
MODEL=/home/chris/models/ornith-1.5-35b-a3b/Ornith-1.5-35B-A3B-Q4_K_M.gguf
[[ -x "$BIN" && -f "$MODEL" ]] || fail 'runtime / GGUF missing'
[[ $(cat "/home/chris/.local/qwen4exp/$REV/bin/source-commit") == "$REV" ]] || fail 'runtime version not pinned'
[[ $(cat /sys/devices/system/node/node0/cpulist) == "0-13,28-41" ]] || fail 'unexpected NUMA0 topology'
[[ $(cat /sys/devices/system/node/node1/cpulist) == "14-27,42-55" ]] || fail 'unexpected NUMA1 topology'
systemctl is-active --quiet mag-llm-qwen.service && fail 'Qwen still running on NUMA1; run ops/ornith/cutover.sh'
systemctl is-active --quiet mag-llm-iq3.service && fail 'IQ3 still running on NUMA1'
for n in 1 2 3 4; do
  systemctl is-active --quiet "mag-llm-ornith@$n.service" && fail "legacy instance $n still running"
done
# 23.5 GiB RSS measured (mlock), interleaved ~12 GiB per node; keep headroom per node.
for node in 0 1; do
  read -r free file shmem < <(awk '$3=="MemFree:"{f=$4} $3=="FilePages:"{p=$4} $3=="Shmem:"{s=$4} END{print f+0,p+0,s+0}' "/sys/devices/system/node/node$node/meminfo")
  usable=$((free + (file > shmem ? file-shmem : 0)))
  (( usable >= 14*1024*1024 )) || fail "NUMA$node reclaimable RAM below 14GiB"
done
bash /usr/local/lib/mag-ornith/verify-model.sh || fail 'model SHA verification failed'
# 131072 = 4 slots x 32k context (same per-bot window as the 4 old instances).
exec /usr/bin/numactl --interleave=all --physcpubind=0-27 "$BIN" \
  --model "$MODEL" --alias ornith-1.5-35b-a3b \
  --host 127.0.0.1 --port 8191 --ctx-size 131072 --parallel 4 \
  --threads 28 --threads-batch 28 --numa distribute \
  --load-mode mlock --jinja --reasoning-format auto --reasoning off \
  --cache-ram 8192 --ctx-checkpoints 8 \
  --slot-save-path /var/lib/mag-llm/slots/ornith --no-ui
