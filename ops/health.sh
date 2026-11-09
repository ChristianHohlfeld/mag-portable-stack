#!/usr/bin/env bash
# Read-only health check (mag-stack-check.timer). Short probes only.
# A bot that is downloading, loading, paused or stopped is a normal state:
# it is printed, never an error. Only the room and its ingress must be up.
set -euo pipefail
systemctl is-active --quiet mag-120.service nginx.service tailscaled.service
for unit in mag-audit-gateway.service mag-modeld.service; do
  if systemctl cat "$unit" >/dev/null 2>&1; then systemctl is-active --quiet "$unit"; fi
done
curl --noproxy '*' --fail --silent --show-error --max-time 5 -o /dev/null http://127.0.0.1:8766/readyz
if [[ -r /run/mag-models/status.json ]]; then
  jq -r '.bots | to_entries[] | "model \(.key): \(.value.state)" + (if .value.detail != "" then " (\(.value.detail))" else "" end)' \
    /run/mag-models/status.json 2>/dev/null || true
fi
echo ok
