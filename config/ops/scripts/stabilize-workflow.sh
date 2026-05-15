#!/usr/bin/env bash
set -euo pipefail

echo "=== Stabilize Workflow $(date) ==="

require_sudo() {
  if sudo -n true 2>/dev/null; then
    return 0
  fi
  echo "Sudo permission required for service management." >&2
  return 1
}

start_if_needed() {
  local svc="$1"
  if systemctl is-active --quiet "$svc"; then
    echo "  OK: $svc running"
    return 0
  fi

  echo "  WARN: $svc not running, starting"
  sudo systemctl start "$svc"

  sleep 1
  if systemctl is-active --quiet "$svc"; then
    echo "  OK: $svc started"
  else
    echo "  ERR: failed to start $svc"
    return 1
  fi
}

wait_http() {
  local name="$1"
  local url="$2"
  local expect="${3:-200}"

  for _ in $(seq 1 30); do
    code="$(curl -sS -o /dev/null -w '%{http_code}' "$url" 2>/dev/null || echo 000)"
    if [ "$code" = "$expect" ]; then
      echo "  OK: $name ($expect)"
      return 0
    fi
    sleep 1
  done

  echo "  ERR: $name expected $expect"
  return 1
}

require_sudo

echo "--- Services ---"
for svc in nginx ollama open-webui comfyui-image comfyui; do
  start_if_needed "$svc"
done

echo "--- Local Endpoints ---"
wait_http "Ollama tags" "http://127.0.0.1:11434/api/tags"
wait_http "open-webui version" "http://127.0.0.1:3000/api/version"
wait_http "ComfyUI FLUX queue" "http://127.0.0.1:8187/queue"
wait_http "ComfyUI WAN queue" "http://127.0.0.1:8188/queue"

echo "--- Route Regression Guard ---"
if /home/chris/ops/tests/regression-comfyui-route.sh; then
  echo "  OK: /comfyui route healthy"
else
  echo "  ERR: /comfyui regression detected"
  exit 1
fi

echo "--- Nginx Config sanity ---"
if sudo nginx -t >/dev/null 2>&1; then
  echo "  OK: nginx config valid"
else
  echo "  ERR: nginx config invalid"
  exit 1
fi

echo "--- Runtime Snapshot ---"
systemctl --type=service --state=running | egrep 'nginx|open-webui|ollama|comfyui' || true
ss -ltnp | egrep ':3000|:8187|:8188|:11434|:443' || true

echo "=== Stabilization Complete ==="
