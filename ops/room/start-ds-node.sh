#!/usr/bin/env bash
set -euo pipefail
NODE=${1:-}
[[ $NODE == 0 || $NODE == 1 ]] || { echo 'usage: start-ds-node.sh 0|1' >&2; exit 4; }
[[ -x $IK_BIN && -f $DS_MODEL && -f $DS_TEMPLATE ]] || { echo 'DeepSeek runtime/model/template missing' >&2; exit 4; }
# MAG_ROOM_MODEL_PORTS is validated by both clients; select by NUMA, not list index.
PORT=
IFS=, read -ra BACKENDS <<< "$MAG_ROOM_MODEL_PORTS"
for BACKEND in "${BACKENDS[@]}"; do
  [[ ${BACKEND#*:} != "$NODE" ]] || PORT=${BACKEND%:*}
done
[[ $PORT =~ ^[0-9]+$ ]] || { echo 'No port configured for NUMA node' >&2; exit 4; }
exec "$IK_BIN" --model "$DS_MODEL" --alias "$MAG_ROOM_MODEL_ID" \
  --host 127.0.0.1 --port "$PORT" --ctx-size "$DS_CONTEXT" --parallel 1 \
  --threads 14 --threads-batch 14 --numa numactl -b 2048 -ub 2048 \
  -rtr -crs 0.20 --ctx-checkpoints-interval 128 --ctx-checkpoints-tolerance 0 \
  --mlock --flash-attn on --cache-type-k f16 --cache-type-v f16 \
  --cache-ram "$DS_CACHE_RAM" --jinja --chat-template-file "$DS_TEMPLATE" \
  --reasoning-format deepseek --slot-save-path "/var/lib/mag-llm/slots/ds-node$NODE" \
  --temp 1.0 --top-p 0.95 --min-p 0.01 --metrics --webui none
