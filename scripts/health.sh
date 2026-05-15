#!/usr/bin/env bash
set -euo pipefail

OLLAMA_PORT="${HOST_OLLAMA_PORT:-11434}"
OPENWEBUI_PORT="${HOST_OPENWEBUI_PORT:-3000}"
COMFY_FLUX_PORT="${HOST_COMFY_FLUX_PORT:-8187}"
COMFY_WAN_PORT="${HOST_COMFY_WAN_PORT:-8188}"
HTTP_PORT="${HOST_HTTP_PORT:-80}"

check() {
  local name="$1"
  local url="$2"
  local code
  code="$(curl -sS -o /dev/null -w "%{http_code}" "$url" 2>/dev/null || echo 000)"
  if [ "$code" = "200" ]; then
    echo "[ok] $name -> $url"
  else
    echo "[fail:$code] $name -> $url"
  fi
}

check ollama "http://127.0.0.1:${OLLAMA_PORT}/api/tags"
check openwebui "http://127.0.0.1:${OPENWEBUI_PORT}/api/version"
check comfy_flux "http://127.0.0.1:${COMFY_FLUX_PORT}/queue"
check comfy_wan "http://127.0.0.1:${COMFY_WAN_PORT}/queue"
check nginx_root "http://127.0.0.1:${HTTP_PORT}/"
check nginx_comfy_route "http://127.0.0.1:${HTTP_PORT}/comfyui/"
