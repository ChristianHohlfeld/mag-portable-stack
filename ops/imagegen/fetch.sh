#!/bin/bash
# FLUX.2 klein 4B (Black Forest Labs, Apache-2.0) at a fixed revision, for diffusers; resumable.
set -e
R=https://huggingface.co/black-forest-labs/FLUX.2-klein-4B/resolve/e7b7dc27
OUT=/home/chris/idol-mi50/weights/flux2-klein-4b
mkdir -p "$OUT"; cd "$OUT"
FILES="LICENSE.md README.md model_index.json scheduler/scheduler_config.json transformer/config.json transformer/diffusion_pytorch_model.safetensors
vae/config.json vae/diffusion_pytorch_model.safetensors text_encoder/config.json text_encoder/generation_config.json text_encoder/model.safetensors.index.json
text_encoder/model-00001-of-00002.safetensors text_encoder/model-00002-of-00002.safetensors tokenizer/tokenizer.json tokenizer/tokenizer_config.json
tokenizer/special_tokens_map.json tokenizer/vocab.json tokenizer/merges.txt tokenizer/added_tokens.json tokenizer/chat_template.jinja"
for f in $FILES; do
  mkdir -p "$(dirname "$f")"
  [ -s "$f" ] && [ ! -e "$f.part" ] && continue
  curl -fsSL --retry 10 --retry-delay 5 -C - -o "$f.part" "$R/$f" && mv "$f.part" "$f" && echo "got $f" || { rm -f "$f.part"; echo "skip $f"; }
done
echo done
