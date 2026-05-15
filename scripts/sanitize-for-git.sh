#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "Sanitizing transient files before Git push"

rm -f open-webui-data/.open-webui/webui.db-wal 2>/dev/null || true
rm -f open-webui-data/.open-webui/webui.db-shm 2>/dev/null || true
find open-webui-data/.open-webui -maxdepth 1 -type f -name 'webui.db.backup*' -delete 2>/dev/null || true
find open-webui-data/.open-webui -maxdepth 1 -type f -name 'webui.db.bak*' -delete 2>/dev/null || true

rm -rf open-webui-data/.open-webui/cache 2>/dev/null || true
rm -rf open-webui-data/.open-webui/uploads 2>/dev/null || true

rm -rf config/openwebui-local-config/__pycache__ 2>/dev/null || true
rm -rf config/ops/archive 2>/dev/null || true
find comfyui -type d -name '__pycache__' -prune -exec rm -rf {} + 2>/dev/null || true

# Remove mirrored TLS/private material before publishing.
rm -f config/nginx-live/tailscale/*.key 2>/dev/null || true
rm -f config/nginx-live/tailscale/*.crt 2>/dev/null || true
rm -rf config/protected 2>/dev/null || true

echo "Done"
