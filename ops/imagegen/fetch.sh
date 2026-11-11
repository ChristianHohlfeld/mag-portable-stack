#!/bin/bash
# FLUX.2 klein 4B (Black Forest Labs, Apache-2.0) at a fixed revision, for diffusers.
# Resumable, one writer only (a lock), and every large file checked against its SHA256.
set -e
R=https://huggingface.co/black-forest-labs/FLUX.2-klein-4B/resolve/e7b7dc27
OUT=/home/chris/idol-mi50/weights/flux2-klein-4b
mkdir -p "$OUT"; cd "$OUT"
exec 9>"$OUT/.fetch.lock"
flock -n 9 || { echo 'another fetch is running' >&2; exit 1; }
declare -A SHA=(
  [transformer/diffusion_pytorch_model.safetensors]=9f29f9edcfdae452a653ffb51a534ca4decd389952c225724ff3b94042612a6e
  [text_encoder/model-00001-of-00002.safetensors]=8c0506e7f4936fa7e26183a4fd8da4e2bdbc5990ba64ae441f965d51228f36ea
  [text_encoder/model-00002-of-00002.safetensors]=82f2bd839378541b0557bfabaf37c7d3d637071fdcb73302dedd7cf61162ce07
  [vae/diffusion_pytorch_model.safetensors]=ca70d2202afe6415bdbcb8793ba8cd99fd159cfe6192381504d6c4d3036e0f04
  [tokenizer/tokenizer.json]=aeb13307a71acd8fe81861d94ad54ab689df773318809eed3cbe794b4492dae4
)
FILES="model_index.json scheduler/scheduler_config.json transformer/config.json transformer/diffusion_pytorch_model.safetensors
vae/config.json vae/diffusion_pytorch_model.safetensors text_encoder/config.json text_encoder/generation_config.json text_encoder/model.safetensors.index.json
text_encoder/model-00001-of-00002.safetensors text_encoder/model-00002-of-00002.safetensors tokenizer/tokenizer.json tokenizer/tokenizer_config.json
tokenizer/special_tokens_map.json tokenizer/vocab.json tokenizer/merges.txt tokenizer/added_tokens.json tokenizer/chat_template.jinja"
for f in $FILES; do
  mkdir -p "$(dirname "$f")"
  if [ ! -s "$f" ]; then
    curl -fsSL --retry 10 --retry-delay 5 -C - -o "$f.part" "$R/$f" && mv "$f.part" "$f" && echo "got $f" || { echo "not there: $f"; continue; }
  fi
  if [ -n "${SHA[$f]:-}" ]; then
    echo "${SHA[$f]}  $f" | sha256sum --quiet -c - || { echo "WRONG $f" >&2; exit 2; }
  fi
done
echo done
