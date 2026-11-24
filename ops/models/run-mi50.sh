#!/usr/bin/env bash
# mag-modeld verifies the local artifact before starting this instance.
set -euo pipefail
bot=${1:?usage: run-mi50.sh <bot>}
CONF=${MAG_MODELS_CONF:-/usr/local/lib/mag-models/models.json}
[[ $bot =~ ^[a-z][a-z0-9]{1,15}$ ]] || exit 78
spec=$(jq -ce --arg b "$bot" '.bots[$b] | select(.gpu == "mi50")' "$CONF") || exit 78
q() { jq -r "$1" <<<"$spec"; }
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

SLOTS="$STATE/slots/$bot"
mkdir -p "$SLOTS"
printf '%s draft=0 mmproj=%s\n' "$("$MODELD" --config "$CONF" spec-hash "$bot")" "${#vision_args[@]}" > "$SLOTS/.spec"
threads=$(q .threads)
# The explicit HIP device makes device initialization a requirement. Full
# offload and fit=off prevent the runtime from reducing the offload.
export HIP_VISIBLE_DEVICES=0
exec taskset -c "$(q .cpus)" python3 -I "$(dirname "$0")/gpu-guard.py" "$SLOTS" "$BIN" \
  --model "$model" --alias "$bot" --host 127.0.0.1 --port "$(q .port)" \
  --ctx-size "$(q .ctx)" --parallel 1 --threads "$threads" --threads-batch "$threads" \
  --device ROCm0 --n-gpu-layers all --fit off --jinja --reasoning-format auto \
  --cache-ram 0 --ctx-checkpoints 0 --swa-full --cache-reuse 256 \
  --batch-size 512 --ubatch-size 512 --slots --slot-save-path "$SLOTS" --no-ui "${vision_args[@]}"
