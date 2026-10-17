#!/usr/bin/env bash
set -euo pipefail
DIR=${MAG_IQ3_DIR:-/home/chris/models/deepseek-v4-UD-IQ3_XXS}
BIN=${MAG_IQ3_BIN:-/home/chris/.local/ik/8d21687cc7-mag-c96826ab/bin/llama-server}
[[ -x "$BIN" && -d "$DIR" ]] || { echo "IQ3: runtime missing"; exit 4; }
shopt -s nullglob
first=( "$DIR"/*-00001-of-00004.gguf )
(( ${#first[@]} == 1 )) || { echo "IQ3: ambiguous first split"; exit 4; }
[[ $(cat /sys/devices/system/node/node0/cpulist) == "0-13,28-41" ]] || { echo "IQ3: unexpected NUMA topology"; exit 4; }
read -r free0 file0 shmem0 < <(awk '$3=="MemFree:" {f=$4} $3=="FilePages:" {p=$4} $3=="Shmem:" {s=$4} END {print f+0,p+0,s+0}' /sys/devices/system/node/node0/meminfo)
usable=$(( free0 + file0 - shmem0 ))
(( usable >= 125*1024*1024 )) || { echo "IQ3: insufficient reclaimable node0 RAM"; exit 4; }
systemctl is-active --quiet mag-llm.service && { echo "IQ3: Q4 must be fully stopped"; exit 4; }
install -d -m 0700 /var/lib/mag-llm/slots/iq3
exec /usr/bin/numactl --cpunodebind=0 --membind=0 "$BIN" \
 --model "${first[0]}" --alias deepseek-v4-flash-0731-iq3 \
 --host 127.0.0.1 --port 8091 --ctx-size 32768 --parallel 1 \
 --threads 16 --threads-batch 16 --numa numactl -b 1024 -ub 1024 \
 --mlock --flash-attn on --cache-type-k f16 --cache-type-v f16 \
 --cache-ram 16384 --jinja \
 --chat-template-file /home/chris/llama-templates/deepseek-ai-DeepSeek-V4-Flash-0731.jinja \
 --reasoning-format deepseek --temp 1.0 --top-p 0.95 --min-p 0.01 \
 --metrics --webui none -rtr --slot-save-path /var/lib/mag-llm/slots/iq3 -crs 0.20
