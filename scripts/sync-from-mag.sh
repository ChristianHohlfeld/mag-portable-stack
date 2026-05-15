#!/usr/bin/env bash
set -euo pipefail

# Requires SSH key-based auth (recommended) or preconfigured agent.
# Example:
#   MAG_HOST=mag.tail3e02ae.ts.net MAG_USER=chris ./scripts/sync-from-mag.sh

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MAG_HOST="${MAG_HOST:-mag.tail3e02ae.ts.net}"
MAG_USER="${MAG_USER:-chris}"
REMOTE="${MAG_USER}@${MAG_HOST}"
MODE="${1:---full}"

if [[ "$MODE" != "--full" && "$MODE" != "--minimal-models" ]]; then
  echo "Usage: $0 [--full|--minimal-models]" >&2
  exit 2
fi

sync_file() {
  local remote_path="$1"
  local local_path="$2"
  mkdir -p "$(dirname "$local_path")"
  rsync -av --progress "$REMOTE:$remote_path" "$local_path"
}

sync_dir() {
  local remote_path="$1"
  local local_path="$2"
  mkdir -p "$local_path"
  rsync -av --delete --progress "$REMOTE:$remote_path/" "$local_path/"
}

echo "Syncing ComfyUI source/runtime tree (excluding venv and .git)"
mkdir -p "$ROOT/comfyui"
rsync -av --delete --progress \
  --exclude 'venv' \
  --exclude '.git' \
  --exclude 'models' \
  "$REMOTE:/home/chris/ComfyUI/" "$ROOT/comfyui/"

echo "Syncing workflows and core configs"
sync_dir "/home/chris/ComfyUI/blueprints" "$ROOT/workflows/blueprints"
sync_file "/home/chris/ComfyUI/user/comfyui-gpu1.db" "$ROOT/config/comfyui-gpu1.db"
sync_file "/home/chris/ComfyUI/user/comfyui-gpu2.db" "$ROOT/config/comfyui-gpu2.db"
sync_file "/home/chris/ComfyUI/user/comfyui.db" "$ROOT/config/comfyui.db"
sync_file "/etc/nginx/sites-available/openwebui-tailnet" "$ROOT/nginx/openwebui-tailnet.conf"
sync_dir "/home/chris/.open-webui" "$ROOT/open-webui-data/.open-webui"
sync_dir "/home/chris/openwebui-local-config" "$ROOT/config/openwebui-local-config"
sync_dir "/home/chris/ops" "$ROOT/config/ops"
sync_dir "/home/chris/.local/bin" "$ROOT/config/bin"
sync_dir "/etc/systemd/system" "$ROOT/config/systemd-live"
sync_dir "/etc/nginx" "$ROOT/config/nginx-live"

if [ "$MODE" = "--full" ]; then
  echo "Syncing full ComfyUI model tree (this can take a long time)"
  sync_dir "/home/chris/ComfyUI/models" "$ROOT/models"
  echo "Syncing full Ollama model store from /usr/share/ollama/.ollama"
  sync_dir "/usr/share/ollama/.ollama" "$ROOT/models/ollama/.ollama"
else
  echo "Syncing required FLUX + WAN model set only"
  sync_file "/home/chris/ComfyUI/models/unet/flux1-schnell-Q3_K_S.gguf" "$ROOT/models/unet/flux1-schnell-Q3_K_S.gguf"
  sync_file "/home/chris/ComfyUI/models/clip/clip_l.safetensors" "$ROOT/models/clip/clip_l.safetensors"
  sync_file "/home/chris/ComfyUI/models/clip/t5xxl_fp8_e4m3fn.safetensors" "$ROOT/models/clip/t5xxl_fp8_e4m3fn.safetensors"
  sync_file "/home/chris/ComfyUI/models/vae/ae.safetensors" "$ROOT/models/vae/ae.safetensors"
  sync_file "/home/chris/ComfyUI/models/diffusion_models/wan2.2_ti2v_5B_fp16.safetensors" "$ROOT/models/diffusion_models/wan2.2_ti2v_5B_fp16.safetensors"
  sync_file "/home/chris/ComfyUI/models/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors" "$ROOT/models/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors"
  sync_file "/home/chris/ComfyUI/models/vae/wan2.2_vae.safetensors" "$ROOT/models/vae/wan2.2_vae.safetensors"
fi

echo "Sync complete"
