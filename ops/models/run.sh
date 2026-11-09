#!/usr/bin/env bash
# Launcher of one CPU-only llama.cpp model server (mag-model@<bot>.service).
# Everything comes from models.json; mag-modeld starts this unit only after
# it verified the GGUF (size against Hugging Face, SHA256 once) and wrote the
# <bot>.model.ok stamp. No GPU: the pinned runtime is built without a GPU
# backend and the MI50 belongs to MAG Idol alone.
set -euo pipefail
bot=${1:?usage: run.sh <bot>}
CONF=${MAG_MODELS_CONF:-/usr/local/lib/mag-models/models.json}
[[ $bot =~ ^[a-z][a-z0-9]{1,15}$ ]] || { echo "model $bot: invalid bot name" >&2; exit 78; }
spec=$(jq -ce --arg b "$bot" '.bots[$b] // empty' "$CONF") || { echo "model $bot: not in $CONF" >&2; exit 78; }
q() { jq -r "$1" <<<"$spec"; }
STATE=$(jq -r '.state_dir' "$CONF")
REV=$(jq -r '.runtime.rev' "$CONF")
BIN="$(jq -r '.runtime.dir' "$CONF")/$REV/bin/llama-server"
[[ $REV =~ ^[a-f0-9]{40}$ && -x $BIN ]] || { echo "model $bot: runtime $REV missing (ops/models/build-runtime.sh)" >&2; exit 78; }
node=$(q .numa); cpus=$(q .cpus); threads=$(q .threads); port=$(q .port); ctx=$(q .ctx)
model=$(q .model.path)

# stamp: "<size> <mtime> <sha256>" written by mag-modeld after verification.
verified() {
  local file=$1 stamp=$2 size mtime
  [[ -f $file && ! -L $file && -r $stamp ]] || return 1
  size=$(stat -c %s "$file"); mtime=$(stat -c %Y "$file")
  read -r s m _ < "$stamp" || return 1
  [[ $s == "$size" && $m == "$mtime" ]]
}
verified "$model" "$STATE/$bot.model.ok" || { echo "model $bot: GGUF not verified yet (mag-modeld)" >&2; exit 78; }

draft_args=()
draft=$(q '.draft.path // empty')
if [[ -n $draft && ! -e $STATE/$bot.draft.off ]] && verified "$draft" "$STATE/$bot.draft.ok"; then
  mapfile -t draft_args < <(q '.draft.args[]')
  draft_args+=(--spec-draft-model "$draft")
fi
mapfile -t extra < <(q '.args[]')

SLOTS="$STATE/slots/$bot"
mkdir -p "$SLOTS"
# What this process was started with; mag-modeld compares it to the installed
# spec ("restart pending") and reports whether the drafter is in use.
printf '%s draft=%s\n' "$("$(dirname "$CONF")/mag-modeld" --config "$CONF" spec-hash "$bot")" "${#draft_args[@]}" > "$SLOTS/.spec" 2>/dev/null || :

# Physical cores of one socket only; the hyperthread siblings 28-55 stay free
# for builds, VMs and Idol. Memory is bound to the same node before the
# weights are read (load-mode mlock: private, node-local, never swapped).
exec numactl --physcpubind="$cpus" --membind="$node" "$BIN" \
  --model "$model" --alias "$bot" --host 127.0.0.1 --port "$port" \
  --ctx-size "$ctx" --parallel 1 --threads "$threads" --threads-batch "$threads" --numa numactl \
  --load-mode mlock --lazy-mode off --device none --n-gpu-layers 0 \
  --jinja --reasoning-format auto \
  --cache-ram 0 --ctx-checkpoints 0 --swa-full \
  --batch-size 512 --ubatch-size 512 \
  "${extra[@]}" "${draft_args[@]}" \
  --slots --slot-save-path "$SLOTS" --no-ui
