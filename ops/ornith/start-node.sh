#!/usr/bin/env bash
# Ornith-1.5-35B-A3B per-NUMA-node server (variant B, ops/ornith/README.md): one model
# copy per node, node N = port 819(1+N), 14 physical cores, memory bound to node N,
# --parallel 2 (two room bots per server, id_slot forced by the gateway).
# Same pinned upstream llama.cpp runtime as Qwen (ops/qwen/runtime.sha).
# Usage: start-node.sh 0|1
set -euo pipefail
NODE=${1:-}
fail(){ echo "Ornith node$NODE: $*" >&2; exit 4; }
[[ $NODE == 0 || $NODE == 1 ]] || fail 'usage: start-node.sh 0|1'
PORT=$((8191 + NODE))
CPUS=$([[ $NODE == 0 ]] && echo 0-13 || echo 14-27)
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
# ~21.8 GiB RSS per server measured (mlock, 43.6 GiB for both) + 2 GiB --cache-ram,
# all on this node (--membind): require 24 GiB reclaimable on node N.
read -r free file shmem < <(awk '$3=="MemFree:"{f=$4} $3=="FilePages:"{p=$4} $3=="Shmem:"{s=$4} END{print f+0,p+0,s+0}' "/sys/devices/system/node/node$NODE/meminfo")
usable=$((free + (file > shmem ? file-shmem : 0)))
(( usable >= 24*1024*1024 )) || fail "NUMA$NODE reclaimable RAM below 24GiB"
bash /usr/local/lib/mag-ornith/verify-model.sh || fail 'model SHA verification failed'
# 65536 = 2 slots x 32k context (same per-bot window as the old instances).
exec /usr/bin/numactl --cpunodebind="$NODE" --membind="$NODE" --physcpubind="$CPUS" "$BIN" \
  --model "$MODEL" --alias ornith-1.5-35b-a3b \
  --host 127.0.0.1 --port "$PORT" --ctx-size 65536 --parallel 2 \
  --threads 14 --threads-batch 14 --numa numactl \
  --load-mode mlock --jinja --reasoning-format auto --reasoning off \
  --cache-ram 2048 --ctx-checkpoints 8 \
  --slot-save-path "/var/lib/mag-llm/slots/ornith-node$NODE" --no-ui
