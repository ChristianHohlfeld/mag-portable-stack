#!/bin/bash
# Removes the private entry; tailscale serve :8446 is unaffected. Keeps /etc/mag-acme (secrets/certs).
set -euo pipefail
systemctl disable --now mag-caddy.service mag-acme-renew.timer 2>/dev/null || true
rm -f /etc/systemd/system/mag-caddy.service /etc/systemd/system/mag-acme-renew.{service,timer}
systemctl daemon-reload
