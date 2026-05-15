#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

docker_compose() {
  if docker compose version >/dev/null 2>&1; then
    docker compose "$@"
  elif command -v docker-compose >/dev/null 2>&1; then
    docker-compose "$@"
  else
    echo "docker compose or docker-compose is required" >&2
    exit 1
  fi
}

echo "==> Building ComfyUI image"
docker_compose build comfyui-flux comfyui-wan

echo "==> Pulling upstream images"
docker_compose pull ollama open-webui nginx

echo "==> Starting stack"
docker_compose up -d

echo "==> Health summary"
"$ROOT/scripts/health.sh"
