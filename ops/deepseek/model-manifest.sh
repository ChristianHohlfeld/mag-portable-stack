# shellcheck shell=bash
# DeepSeek-V4-Flash-0731, unsloth UD-Q4_K_XL – Soll-Werte (Quelle: Hugging-Face-API
# https://huggingface.co/api/models/unsloth/DeepSeek-V4-Flash-0731-GGUF/tree/main/UD-Q4_K_XL).
# Von verify-model.sh und model-gate.sh eingebunden.
MODEL_DIR=${MODEL_DIR:-/home/chris/models/deepseek-v4}
MODEL_PREFIX="DeepSeek-V4-Flash-0731-UD-Q4_K_XL"
MODEL_PARTS=(00001 00002 00003 00004 00005)
declare -A MODEL_SIZE=(
  [00001]=5257408
  [00002]=48935523072
  [00003]=48980787136
  [00004]=49999168416
  [00005]=7174505088
)
declare -A MODEL_SHA=(
  [00001]=d13ce8f90855547bdaebe7312f531a1f2c4f822178d3103951f27fe884395cfa
  [00002]=d5b61668950f4743aacd677675d7fcf7507dbe1db6d304e8ff97ed1f00827bee
  [00003]=9705db7e589f360685ca7bd48100b270d78d228d4f5aa980508f3b2778af5494
  [00004]=7f13a68e3ca64208454c4ba32cc2757c0cbe78e3e5576c3142bf7007ca97da42
  [00005]=ed0d93164d3784968d6ce40d6d201ba98337f16e7db1b31fe495b2b0f334cc09
)
model_part() { echo "$MODEL_DIR/${MODEL_PREFIX}-$1-of-00005.gguf"; }
model_total_bytes() { local t=0 p; for p in "${MODEL_PARTS[@]}"; do t=$((t + MODEL_SIZE[$p])); done; echo $t; }
