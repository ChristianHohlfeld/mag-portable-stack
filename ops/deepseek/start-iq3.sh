#!/usr/bin/env bash
set -euo pipefail
DIR=${MAG_IQ3_DIR:-/home/chris/models/deepseek-v4-UD-IQ3_XXS}
BIN=${MAG_IQ3_BIN:-/home/chris/.local/ik/8d21687cc7-mag-c96826ab/bin/llama-server}
[[ -x "$BIN" && -d "$DIR" ]] || { echo "IQ3: runtime/model missing" >&2;exit 4; }
[[ $(cat /sys/devices/system/node/node1/cpulist) == "14-27,42-55" ]] || { echo "IQ3: NUMA1 topology mismatch" >&2;exit 4; }
read -r free file shmem < <(awk '$3=="MemFree:"{f=$4} $3=="FilePages:"{p=$4} $3=="Shmem:"{s=$4} END{print f+0,p+0,s+0}' /sys/devices/system/node/node1/meminfo)
usable=$(( free + (file>shmem ? file-shmem : 0) ))
(( usable >= 188*1024*1024 )) || { echo "IQ3: NUMA1 low on RAM (backup reserve)" >&2;exit 4; }
for u in mag-backup-daily mag-backup-weekly mag-backup-monthly mag-backup-restore-test mag-backup-deploy mag-backup-clear;do
  systemctl is-active --quiet "$u.service" && { echo "IQ3: backup running ($u), retry later" >&2;exit 4; }
done
first="$DIR/DeepSeek-V4-Flash-0731-UD-IQ3_XXS-00001-of-00004.gguf"
[[ -f "$first" ]] || exit 4
exec /usr/bin/numactl --cpunodebind=1 --membind=1 "$BIN" \
 --model "$first" --alias deepseek-v4-flash-0731-iq3 \
 --host 127.0.0.1 --port 8091 --ctx-size 32768 --parallel 1 \
 --threads 12 --threads-batch 12 --numa numactl -b 1024 -ub 1024 \
 --mlock --flash-attn on --cache-type-k f16 --cache-type-v f16 \
 --cache-ram 16384 --jinja \
 --chat-template-file /home/chris/llama-templates/deepseek-ai-DeepSeek-V4-Flash-0731.jinja \
 --reasoning-format deepseek --temp 1.0 --top-p 0.95 --min-p 0.01 \
 --metrics --webui none -rtr --slot-save-path /var/lib/mag-llm/slots/iq3 -crs 0.20
