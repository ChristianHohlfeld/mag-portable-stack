#!/usr/bin/env bash
# Ornith-1.5-35B-A3B instance N (1..4): 7 physical cores + local RAM, 2 per NUMA node.
# N=1 cpus 0-6 / N=2 7-13 on node0, N=3 14-20 / N=4 21-27 on node1; port 8190+N.
# Same pinned upstream llama.cpp runtime as Qwen (ops/qwen/runtime.sha).
set -euo pipefail
N=${1:-}
fail(){ echo "Ornith@$N: $*" >&2; exit 4; }
case "$N" in
  1) CPUS=0-6 NODE=0 ;;
  2) CPUS=7-13 NODE=0 ;;
  3) CPUS=14-20 NODE=1 ;;
  4) CPUS=21-27 NODE=1 ;;
  *) fail 'instance must be 1..4' ;;
esac
PORT=$((8190 + N))
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
# ~23 GiB RSS per instance (mlock); keep headroom for the node and backups.
read -r free file shmem < <(awk '$3=="MemFree:"{f=$4} $3=="FilePages:"{p=$4} $3=="Shmem:"{s=$4} END{print f+0,p+0,s+0}' "/sys/devices/system/node/node$NODE/meminfo")
usable=$((free + (file > shmem ? file-shmem : 0)))
(( usable >= 26*1024*1024 )) || fail "NUMA$NODE reclaimable RAM below 26GiB"
bash /usr/local/lib/mag-ornith/verify-model.sh || fail 'model SHA verification failed'
exec /usr/bin/numactl --physcpubind=$CPUS --membind=$NODE "$BIN" \
  --model "$MODEL" --alias ornith-1.5-35b-a3b \
  --host 127.0.0.1 --port "$PORT" --ctx-size 32768 --parallel 1 \
  --threads 7 --threads-batch 7 --numa numactl \
  --load-mode mlock --jinja --reasoning-format auto --reasoning off \
  --cache-ram 4096 --ctx-checkpoints 8 \
  --slot-save-path "/var/lib/mag-llm/slots/ornith$N" --no-ui
