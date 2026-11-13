#!/bin/bash
# HyperFlow H3 Flash (Video Rebirth), a few-step MiniMax H3 that makes video
# with its sound, at fixed revisions. Its licence is the MiniMax H3 Community
# License, which leaves out the EU; MiniMax authorised Chris personally on
# 2026-11-13 (private, non-commercial, nothing passed on, output marked as made
# by AI, no training of other models). So: these weights are fetched only onto
# his own host and never into this repository or a backup that leaves it.
# The transformers as GGUF Q8_0 (fl2va: words, a first frame; ref2va: up to
# nine reference pictures), the MiniMax-H3 Qwen3-VL-32B text encoder as GGUF
# Q4_K_M, the video VAE in fp16 and the audio VAE in fp32. Resumable, one writer
# only (a lock), every file checked against its SHA256. Never deletes anything.
set -e
OUT=${MAG_IDOL_BASE:-$HOME/idol-mi50}/weights/hyperflow-h3
HERE=$(dirname "$(readlink -f "$0")")
mkdir -p "$OUT"; cd "$OUT"
exec 9>"$OUT/.fetch.lock"
flock -n 9 || { echo 'another fetch is running' >&2; exit 1; }
FLASH=https://huggingface.co/VideoRebirthAI/hyperflow-h3-flash/resolve/c8f74e481b9f7743cd91d2d3c4bf108eeb723748
GGUF=https://huggingface.co/leejet/MiniMax-H3-GGUF/resolve/d9c4c6312b4728a68a15a35626d84775a6523783
COMFY=https://huggingface.co/Comfy-Org/MiniMax-H3/resolve/54472221d27c695fca9621b0c3ed52f06c4af707
declare -A SHA=(
  [fl2va.gguf]=9b7aebc17bd1be998bb82aa65409514c85e97e7b63559b75f206c44db17f7b2b
  [ref2va.gguf]=fc7c98be8a1197092fe491678d3f71d7514fe037a95a7d9d518bba6f21476a40
  [text_encoder.gguf]=11e6efe70a57ce7f4838c47bdbd1a1c4b8ce10e2b7747f1b065990b70f4b05fc
  [video_vae.safetensors]=7c1f131492e7eddacaac9069a61b81bdd39de5cc96561e677c5eab1cdce5e522
  [audio_vae.safetensors]=8e505d95dd1561d47abd43d4238fd40d9bb1ae9e147ed0a4cba778d76ae4db48
  [taeh3.safetensors]=4fd022bfcab08772fe0536b17ea1a3bbb5625be11e397868d1c5d891863d4c13
)
get() {  # get LOCAL URL
  if [ ! -s "$1" ]; then
    curl -fsSL --retry 20 --retry-delay 10 -C - -o "$1.part" "$2" && mv "$1.part" "$1" && echo "got $1" || { echo "not there: $1" >&2; exit 3; }
  fi
  echo "${SHA[$1]}  $1" | sha256sum --quiet -c - || { echo "WRONG $1" >&2; exit 2; }
}
# Each file has one writer; the files come side by side (one connection each).
get audio_vae.safetensors "$COMFY/vae/minimax_h3_audio_vae_fp32.safetensors" &
get video_vae.safetensors "$COMFY/vae/minimax_h3_video_vae_fp16.safetensors" &
get ref2va.gguf "$FLASH/ref2va_gguf/minimax_h3_ref2va_hyperflow_flash_14b-Q8_0.gguf" &
get fl2va.gguf "$FLASH/fl2va_gguf/minimax_h3_fl2va_hyperflow_flash_14b-Q8_0.gguf" &
get text_encoder.gguf "$GGUF/qwen3vl_32b_minimax_h3-Q4_K_M.gguf" &
# madebyollin's tiny autoencoder for H3 (MIT): decodes the video in seconds instead of minutes.
get taeh3.safetensors "https://raw.githubusercontent.com/madebyollin/taehv/62f7591f59dfbb4c3c02b7a621d180a9eeaba26c/safetensors/taeh3.safetensors" &
failed=0
for job in $(jobs -p); do wait "$job" || failed=1; done
(( failed == 0 )) || { echo 'not complete' >&2; exit 1; }
touch .complete
echo done
# the small model that writes typed prompts in H3's form (Apache-2.0)
"$HERE/fetch-rewriter.sh" || echo "rewriter not fetched; h3gen uses its rules" >&2
