#!/usr/bin/env bash
# mag-modeld verifies the local artifact before starting this instance.
set -euo pipefail
bot=${1:?usage: run-mi50.sh <bot>}
CONF=${MAG_MODELS_CONF:-/usr/local/lib/mag-models/models.json}
[[ $bot =~ ^[a-z][a-z0-9]{1,15}$ ]] || exit 78
spec=$(jq -ce --arg b "$bot" '.bots[$b] | select(.gpu == "mi50")' "$CONF") || exit 78
q() { jq -r "$@" <<<"$spec"; }
MODELD="$(dirname "$CONF")/mag-modeld"
STATE=$(jq -r '.state_dir' "$CONF")
REV=$(jq -r '.runtime.rev' "$CONF")
BIN="$(jq -r '.runtime.dir' "$CONF")/$REV/hip-gfx906/bin/llama-server"
[[ $REV =~ ^[a-f0-9]{40}$ && -x $BIN ]] || { echo "model $bot: the gfx906 HIP runtime is missing" >&2; exit 78; }

model=$("$MODELD" --config "$CONF" model-path "$bot") || exit 78
verified() {
  local file=$1 stamp=$2 size mtime hash recorded
  [[ -f $file && ! -L $file && -r $stamp ]] || return 1
  read -r size mtime hash recorded < "$stamp" || return 1
  [[ $recorded == "$file" && $size == "$(stat -c %s "$file")" && $mtime == "$(stat -c %Y "$file")" ]]
}

verified "$model" "$STATE/$bot.model.ok" || exit 78

vision_args=()
mmproj=$(q '.mmproj.path // empty')
if [[ -n $mmproj && ! -e $STATE/$bot.mmproj.off ]] && verified "$mmproj" "$STATE/$bot.mmproj.ok"; then
  # Keep the projector off the MI50 to preserve VRAM for Qwen.
  vision_args=(--mmproj "$mmproj" --no-mmproj-offload)
fi

mapfile -d '' -t extra < <(q -j '.args[]? | ., "\u0000"')
mapfile -d '' -t mtp_args < <(q -j '.mtp_args[]? | ., "\u0000"')
here=$(dirname "$0")
if [[ -e $STATE/$bot.draft.off ]]; then
  mtp_args=()
elif (("${#mtp_args[@]}")) && ! python3 -I "$here/mi50-mtp.py" probe "$model"; then
  echo "model $bot: no NextN/MTP tensors detected; starting without MTP" >&2
  mtp_args=()
fi

SLOTS="$STATE/slots/$bot"
mkdir -p "$SLOTS"
printf '%s draft=%s mmproj=%s\n' "$("$MODELD" --config "$CONF" spec-hash "$bot")" "${#mtp_args[@]}" "${#vision_args[@]}" > "$SLOTS/.spec"
threads=$(q .threads)
port=$(q .port)
# The explicit HIP device makes device initialization a requirement. Full
# offload and fit=off prevent the runtime from reducing the offload.
export HIP_VISIBLE_DEVICES=0
command=("$BIN"
  --model "$model" --alias "$bot" --host 127.0.0.1 --port "$port"
  --ctx-size "$(q .ctx)" --parallel 1 --threads "$threads" --threads-batch "$threads"
  --device ROCm0 --n-gpu-layers all --fit off --jinja --reasoning-format auto
  --cache-ram 0 --ctx-checkpoints 0 --swa-full --cache-reuse 256
  --batch-size 512 --ubatch-size 512 --slots --slot-save-path "$SLOTS" --no-ui
  "${extra[@]}" "${vision_args[@]}")
if (("${#mtp_args[@]}")); then
  # Both attempts run in the same guarded process group under the GPU lock.
  command=(python3 -I "$here/mi50-mtp.py" "$port" "$SLOTS/.spec" "${#mtp_args[@]}" "${command[@]}" "${mtp_args[@]}")
fi
exec taskset -c "$(q .cpus)" python3 -I "$here/gpu-guard.py" "$SLOTS" "${command[@]}"
