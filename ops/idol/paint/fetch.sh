#!/bin/bash
# The weights of the paint stage (paint.py), at fixed revisions, fetched with
# curl (resumable) and checked against the SHA256 Hugging Face lists for each:
#   SDXL base 1.0 (CreativeML Open RAIL++-M), its fp16-safe VAE (MIT) and
#   MV-Adapter's image-and-geometry adapter (Apache-2.0).
# One writer: a second run waits for the first. Nothing is ever deleted.
set -euo pipefail
BASE=${MAG_IDOL_BASE:-$HOME/idol-mi50}
OUT=$BASE/weights/paint
mkdir -p "$OUT"
exec 9>"$OUT/.fetch.lock"
flock 9
get() {  # repo revision path -> $OUT/<repo name>/<path>
  local repo=$1 rev=$2 path=$3 dir file want got
  dir=$OUT/${repo##*/}
  file=$dir/$path
  mkdir -p "$(dirname "$file")"
  want=$(curl -fsS "https://huggingface.co/api/models/$repo/paths-info/$rev" -d "paths=$path" |
         python3 -c 'import json,sys; d=json.load(sys.stdin)[0]; print((d.get("lfs") or {}).get("oid", ""))')
  if [[ -s $file && ! -e $file.part ]]; then
    [[ -z $want ]] && return 0
    [[ -e $file.ok ]] && return 0
  else
    curl -fsSL --retry 10 --retry-delay 5 -C - -o "$file.part" "https://huggingface.co/$repo/resolve/$rev/$path"
    mv "$file.part" "$file"
  fi
  if [[ -n $want ]]; then
    got=$(sha256sum "$file" | cut -d' ' -f1)
    if [[ $got != "$want" ]]; then
      mv "$file" "$file.bad"
      echo "SHA256 of $repo/$path does not match; kept as .bad" >&2
      return 1
    fi
    touch "$file.ok"
  fi
  echo "got $repo/$path"
}
SDXL=stabilityai/stable-diffusion-xl-base-1.0 R=462165984030d82259a11f4367a4eed129e94a7b
for f in model_index.json scheduler/scheduler_config.json \
         text_encoder/config.json text_encoder/model.fp16.safetensors \
         text_encoder_2/config.json text_encoder_2/model.fp16.safetensors \
         tokenizer/merges.txt tokenizer/special_tokens_map.json tokenizer/tokenizer_config.json tokenizer/vocab.json \
         tokenizer_2/merges.txt tokenizer_2/special_tokens_map.json tokenizer_2/tokenizer_config.json tokenizer_2/vocab.json \
         unet/config.json unet/diffusion_pytorch_model.fp16.safetensors; do
  get $SDXL $R "$f"
done
get madebyollin/sdxl-vae-fp16-fix 207b116dae70ace3637169f1ddd2434b91b3a8cd config.json
get madebyollin/sdxl-vae-fp16-fix 207b116dae70ace3637169f1ddd2434b91b3a8cd diffusion_pytorch_model.safetensors
get huanngzh/mv-adapter 6de4033df6b53366f3c009d22f5ec434bb55e59f mvadapter_ig2mv_sdxl.safetensors
# MV-Adapter's texture script makes every view twice as large with Real-ESRGAN
# x2plus (BSD-3-Clause) before laying it on; its release lists no checksum,
# so the one of the file it published is written here.
ESR=$OUT/realesrgan/RealESRGAN_x2plus.pth
if [[ ! -e $ESR.ok ]]; then
  mkdir -p "$(dirname "$ESR")"
  curl -fsSL --retry 10 --retry-delay 5 -C - -o "$ESR.part" https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.1/RealESRGAN_x2plus.pth
  if [[ $(sha256sum "$ESR.part" | cut -d' ' -f1) == 49fafd45f8fd7aa8d31ab2a22d14d91b536c34494a5cfe31eb5d89c2fa266abb ]]; then
    mv "$ESR.part" "$ESR" && touch "$ESR.ok" && echo "got RealESRGAN_x2plus.pth"
  else
    mv "$ESR.part" "$ESR.bad"; echo "SHA256 of RealESRGAN_x2plus.pth does not match; kept as .bad" >&2; exit 1
  fi
fi
touch "$OUT/.complete"
echo done
