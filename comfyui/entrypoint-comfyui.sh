#!/usr/bin/env bash
set -euo pipefail

PORT="${1:-8187}"
DB_PATH="${2:-/data/comfyui.db}"

mkdir -p "$(dirname "$DB_PATH")" /opt/comfyui/output /opt/comfyui/input

exec /opt/comfyui/venv/bin/python /opt/comfyui/main.py \
  --listen 0.0.0.0 \
  --port "$PORT" \
  --disable-auto-launch \
  --database-url "sqlite:///$DB_PATH"
