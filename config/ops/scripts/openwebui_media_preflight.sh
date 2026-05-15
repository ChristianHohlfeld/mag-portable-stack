#!/usr/bin/env bash
set -Eeuo pipefail

COMFY_ROOT="/home/chris/ComfyUI"
MODEL_ROOT="$COMFY_ROOT/models"

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "missing command: $1" >&2
    return 1
  }
}

install_pkg_if_missing() {
  local cmd="$1" pkg="$2"
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "installing package: $pkg"
    sudo apt-get update -y
    sudo apt-get install -y "$pkg"
  fi
}

download_if_missing() {
  local target="$1" url="$2"
  if [[ -s "$target" ]]; then
    echo "ok model: $target"
    return 0
  fi
  echo "downloading: $target"
  mkdir -p "$(dirname "$target")"
  local tmp="${target}.part"
  curl -fL --retry 5 --retry-delay 5 -C - -o "$tmp" "$url"
  mv "$tmp" "$target"
}

check_node_available() {
  local base="$1" node="$2"
  curl -fsS "$base/object_info" | jq -e --arg n "$node" 'has($n)' >/dev/null
}

main() {
  install_pkg_if_missing curl curl
  install_pkg_if_missing jq jq

  need_cmd curl
  need_cmd jq

  # Required models for locked image/video flows
  download_if_missing \
    "$MODEL_ROOT/unet/flux1-schnell-Q3_K_S.gguf" \
    "https://huggingface.co/city96/FLUX.1-schnell-gguf/resolve/main/flux1-schnell-Q3_K_S.gguf"

  download_if_missing \
    "$MODEL_ROOT/clip/clip_l.safetensors" \
    "https://huggingface.co/comfyanonymous/flux_text_encoders/resolve/main/clip_l.safetensors"

  download_if_missing \
    "$MODEL_ROOT/clip/t5xxl_fp8_e4m3fn.safetensors" \
    "https://huggingface.co/comfyanonymous/flux_text_encoders/resolve/main/t5xxl_fp8_e4m3fn.safetensors"

  download_if_missing \
    "$MODEL_ROOT/vae/ae.safetensors" \
    "https://huggingface.co/black-forest-labs/FLUX.1-schnell/resolve/main/ae.safetensors"

  download_if_missing \
    "$MODEL_ROOT/diffusion_models/wan2.2_ti2v_5B_fp16.safetensors" \
    "https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/diffusion_models/wan2.2_ti2v_5B_fp16.safetensors"

  download_if_missing \
    "$MODEL_ROOT/text_encoders/umt5-xxl-enc-fp8_e4m3fn.safetensors" \
    "https://huggingface.co/Kijai/WanVideo_comfy/resolve/main/umt5-xxl-enc-fp8_e4m3fn.safetensors"

  download_if_missing \
    "$MODEL_ROOT/vae/wan2.2_vae.safetensors" \
    "https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/vae/wan2.2_vae.safetensors"

  # Runtime availability checks (nodes)
  check_node_available "http://127.0.0.1:8187" "UnetLoaderGGUF"
  check_node_available "http://127.0.0.1:8187" "DualCLIPLoaderGGUF"
  check_node_available "http://127.0.0.1:8188" "WanVideoModelLoader"
  check_node_available "http://127.0.0.1:8188" "LoadWanVideoT5TextEncoder"
  check_node_available "http://127.0.0.1:8188" "VHS_VideoCombine"

  echo "preflight: PASS"
}

main "$@"
