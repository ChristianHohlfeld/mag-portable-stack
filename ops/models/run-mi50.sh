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
draft_disabled=false
if [[ -e $STATE/$bot.draft.off ]]; then draft_disabled=true; fi
REV=$(jq -r '.runtime.rev' "$CONF")
runtime_dir=$(jq -r '.runtime.dir' "$CONF")
BIN="$runtime_dir/$REV/hip-gfx906/bin/llama-server"
variant=$(q '.gpu_runtime_variant // "upstream"')
device_ids=0
device_names=ROCm0
split_args=()
dflash_args=()
case "$variant" in
  upstream) ;;
  gfx906-dev-v2)
    exp_rev=36e84ddb7e5792af6994ee79e12452ba4c918e63
    BIN="$runtime_dir/$exp_rev/hip-gfx906-dev-v2/bin/llama-server"
    [[ $(cat "$(dirname "$BIN")/source-commit" 2>/dev/null) == "$exp_rev" ]] || exit 78
    [[ $(q '.gfx906_experiment.validated // false') == true ]] || { echo "Experimental runtime needs explicit validation" >&2; exit 78; }
    # Physical HIP ordinals are deliberately explicit; after visibility filtering
    # the runtime sees logical ROCm0/ROCm1 in the selected order.
    device_ids=$(q '.gfx906_experiment.devices | join(",")')
    [[ $device_ids =~ ^[0-9]+(,[0-9]+)?$ ]] || exit 78
    if [[ $device_ids == *,* ]]; then
      [[ ${device_ids%,*} != "${device_ids#*,}" ]] || exit 78
      # Both transport cases are intentional: false permits the baseline on
      # hardware without verified P2P; true records measured native P2P.
      # This is an attestation, not a switch controlling HIP peer transfers.
      case $(q '.gfx906_experiment.native_p2p_verified | if type == "boolean" then . else "missing" end') in
        true) echo "model $bot: dual MI50 with verified native P2P" >&2 ;;
        false) echo "model $bot: dual MI50 baseline, native P2P not verified" >&2 ;;
        *) echo "Set native_p2p_verified explicitly to true or false" >&2; exit 78 ;;
      esac
      device_names=ROCm0,ROCm1
      split_args=(--split-mode tensor --main-gpu 0 --pipeline-parallel off)
    fi
    export HSA_XNACK=0 HSA_ENABLE_SDMA=1 HIP_GRAPH=1
    export GGML_CUDA_FATTN_PATH=force_convert GGML_CUDA_CUBLAS_COMPUTE_TYPE=f16
    repack=$(q '.gfx906_experiment | if has("q8_repack") then .q8_repack else true end')
    case "$repack" in true) export GGML_CUDA_Q8_REPACK=1 ;; false) export GGML_CUDA_Q8_REPACK=0 ;; *) exit 78 ;; esac
    if [[ $draft_disabled == true ]]; then
      unset LLAMA_DFLASH_MIRROR_OUTPUT
    else
      draft_path=$(q '.gfx906_experiment.dflash.path // empty')
      if [[ -n $draft_path ]]; then
        draft_sha=$(q '.gfx906_experiment.dflash.sha256 // empty')
        [[ $draft_sha =~ ^[a-f0-9]{64}$ && -f $draft_path && ! -L $draft_path ]] || exit 78
        printf '%s  %s\n' "$draft_sha" "$draft_path" | sha256sum --check --status || exit 78
        export LLAMA_DFLASH_MIRROR_OUTPUT=1
        dflash_args=(-md "$draft_path" --spec-type draft-dflash --spec-draft-n-max 4 --spec-draft-override-tensor '.*=ROCm0' --spec-draft-device ROCm0 -ngld 99)
      fi
    fi
    ;;
  *) echo "Unknown GPU runtime variant: $variant" >&2; exit 78 ;;
esac
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
if [[ $draft_disabled == true ]]; then
  mtp_args=()
  dflash_args=()
elif ((${#dflash_args[@]})); then
  mtp_args=()
elif (("${#mtp_args[@]}")) && ! python3 -I "$here/mi50-mtp.py" probe "$model"; then
  echo "model $bot: no NextN/MTP tensors detected; starting without MTP" >&2
  mtp_args=()
fi

SLOTS="$STATE/slots/$bot"
SAVE_SLOTS="$SLOTS"
# Keep supervisor/guard status at its original path; isolate serialized slots.
if [[ $variant != upstream ]]; then SAVE_SLOTS="$SLOTS/$variant"; fi
mkdir -p "$SAVE_SLOTS"
printf '%s draft=%s mmproj=%s runtime=%s\n' "$("$MODELD" --config "$CONF" spec-hash "$bot")" "$(("${#mtp_args[@]}" + "${#dflash_args[@]}"))" "${#vision_args[@]}" "$variant" > "$SLOTS/.spec"
threads=$(q .threads)
port=$(q .port)
# The explicit HIP device makes device initialization a requirement. Full
# offload and fit=off prevent the runtime from reducing the offload.
export HIP_VISIBLE_DEVICES="$device_ids"
command=("$BIN"
  --model "$model" --alias "$bot" --host 127.0.0.1 --port "$port"
  --ctx-size "$(q .ctx)" --parallel 1 --threads "$threads" --threads-batch "$threads"
  --device "$device_names" --n-gpu-layers all --fit off --jinja --reasoning-format auto
  "${split_args[@]}"
  --cache-ram 0 --ctx-checkpoints 0 --swa-full --cache-reuse 256
  --batch-size 512 --ubatch-size 512 --slots --slot-save-path "$SAVE_SLOTS" --no-ui
  "${extra[@]}" "${vision_args[@]}" "${dflash_args[@]}")
if (("${#mtp_args[@]}")); then
  # Both attempts run in the same guarded process group under the GPU lock.
  command=(python3 -I "$here/mi50-mtp.py" "$port" "$SLOTS/.spec" "${#mtp_args[@]}" "${command[@]}" "${mtp_args[@]}")
fi
exec taskset -c "$(q .cpus)" python3 -I "$here/gpu-guard.py" "$SLOTS" "${command[@]}"
