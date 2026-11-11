#!/bin/bash
# Wan 2.2 I2V-A14B (Wan-AI, Apache-2.0) for a video from a picture, at fixed
# revisions: the pipeline's parts from the Diffusers repository, its two
# experts as GGUF Q6_K (QuantStack, Apache-2.0) and the Lightning 4-step LoRA
# (lightx2v, Apache-2.0). Resumable, one writer only (a lock), every large
# file checked against its SHA256. Never deletes anything.
set -e
OUT=/home/chris/idol-mi50/weights/wan22-i2v
mkdir -p "$OUT"; cd "$OUT"
exec 9>"$OUT/.fetch.lock"
flock -n 9 || { echo 'another fetch is running' >&2; exit 1; }
BASE=https://huggingface.co/Wan-AI/Wan2.2-I2V-A14B-Diffusers/resolve/596658fd9ca6b7b71d5057529bbf319ecbc61d74
GGUF=https://huggingface.co/QuantStack/Wan2.2-I2V-A14B-GGUF/resolve/6c6717459277b9cd1f72579d78a0fd62a79e57dc
LORA=https://huggingface.co/lightx2v/Wan2.2-Lightning/resolve/18bccf8884ec0a078eed79785eb4ef13ea16ce1e/Wan2.2-I2V-A14B-4steps-lora-rank64-Seko-V1
declare -A SHA=(
  [text_encoder/model-00001-of-00003.safetensors]=a8e861969c7433e707cc5a74065d795d36cca07ec96eb6763eb4083df7248f58
  [text_encoder/model-00002-of-00003.safetensors]=d57d948ece4837d850b7a859a4415121d57cacf8b9ee1d4db200c67f592902d7
  [text_encoder/model-00003-of-00003.safetensors]=0da9ee284e21d1406df708788db1d502d95d75f69faa25cd26151bf8829b7c5f
  [tokenizer/spiece.model]=e3909a67b780650b35cf529ac782ad2b6b26e6d1f849d3fbb6a872905f452458
  [tokenizer/tokenizer.json]=20a46ac256746594ed7e1e3ef733b83fbc5a6f0922aa7480eda961743de080ef
  [vae/diffusion_pytorch_model.safetensors]=d6e524b3fffede1787a74e81b30976dce5400c4439ba64222168e607ed19e793
  [gguf/high.gguf]=33ab8d99b5be745d2c939055f541d09108befe3467802fdbbf22ba8e4e62383c
  [gguf/low.gguf]=962d9c937a6e882562cd234ba87c5cfc19d6e99efbf0fc31b4dd9e43eb493ae6
  [lora/high_noise_model.safetensors]=d176c808d6fc461999b68e321efcb7501b20b8c3797523ed0df14f7d1deff11e
  [lora/low_noise_model.safetensors]=024f21de095bc8fad9809ded3e9e49a2e170dcf27075da8145ba7d60d8aab7f9
)
get() {  # get LOCAL URL
  mkdir -p "$(dirname "$1")"
  if [ ! -s "$1" ]; then
    curl -fsSL --retry 20 --retry-delay 10 -C - -o "$1.part" "$2" && mv "$1.part" "$1" && echo "got $1" || { echo "not there: $1" >&2; exit 3; }
  fi
  if [ -n "${SHA[$1]:-}" ]; then
    echo "${SHA[$1]}  $1" | sha256sum --quiet -c - || { echo "WRONG $1" >&2; exit 2; }
  fi
}
for f in model_index.json scheduler/scheduler_config.json text_encoder/config.json text_encoder/model.safetensors.index.json \
         text_encoder/model-00001-of-00003.safetensors text_encoder/model-00002-of-00003.safetensors text_encoder/model-00003-of-00003.safetensors \
         tokenizer/special_tokens_map.json tokenizer/spiece.model tokenizer/tokenizer.json tokenizer/tokenizer_config.json \
         transformer/config.json transformer_2/config.json vae/config.json vae/diffusion_pytorch_model.safetensors; do
  get "$f" "$BASE/$f"
done
get gguf/high.gguf "$GGUF/HighNoise/Wan2.2-I2V-A14B-HighNoise-Q6_K.gguf"
get gguf/low.gguf "$GGUF/LowNoise/Wan2.2-I2V-A14B-LowNoise-Q6_K.gguf"
get lora/high_noise_model.safetensors "$LORA/high_noise_model.safetensors"
get lora/low_noise_model.safetensors "$LORA/low_noise_model.safetensors"
touch "$OUT/.complete"
echo done
