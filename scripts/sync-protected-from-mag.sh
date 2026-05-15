#!/usr/bin/env bash
set -euo pipefail

# Pulls root-protected assets via SSH + sudo tar.
# Requires key-based SSH access and MAG_SUDO_PASSWORD exported.
#
# Example:
#   export MAG_HOST=mag.tail3e02ae.ts.net
#   export MAG_USER=chris
#   export MAG_SUDO_PASSWORD='your-sudo-password'
#   ./scripts/sync-protected-from-mag.sh

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MAG_HOST="${MAG_HOST:-mag.tail3e02ae.ts.net}"
MAG_USER="${MAG_USER:-chris}"
REMOTE="${MAG_USER}@${MAG_HOST}"
MAG_SUDO_PASSWORD="${MAG_SUDO_PASSWORD:-}"

if [ -z "$MAG_SUDO_PASSWORD" ]; then
  echo "MAG_SUDO_PASSWORD is required" >&2
  exit 2
fi

TMP_REMOTE_DIR="/home/${MAG_USER}/stack-export"
TMP_ARCHIVE="${TMP_REMOTE_DIR}/protected-sync.tar"
TMP_LOCAL_ARCHIVE="${ROOT}/config/protected-sync.tar"

echo "Preparing remote archive with root-protected files"
ssh "$REMOTE" "set -euo pipefail; mkdir -p '$TMP_REMOTE_DIR'; rm -f '$TMP_ARCHIVE'; echo '$MAG_SUDO_PASSWORD' | sudo -S -p '' tar -C / -cf '$TMP_ARCHIVE' \
  etc/nginx/tailscale \
  etc/nginx/sites-available/openwebui-tailnet \
  etc/systemd/system/comfyui-image.service \
  etc/systemd/system/comfyui.service \
  etc/systemd/system/open-webui.service \
  etc/systemd/system/ollama.service \
  etc/systemd/system/set-gpu-compute-mode.service \
  usr/share/ollama/.ollama"

echo "Copying protected archive"
scp "$REMOTE:$TMP_ARCHIVE" "$TMP_LOCAL_ARCHIVE"

echo "Extracting protected archive into bundle config/"
mkdir -p "$ROOT/config/protected"
tar -xf "$TMP_LOCAL_ARCHIVE" -C "$ROOT/config/protected"
rm -f "$TMP_LOCAL_ARCHIVE"

echo "Cleaning remote temporary archive"
ssh "$REMOTE" "rm -f '$TMP_ARCHIVE'"

echo "Done"
