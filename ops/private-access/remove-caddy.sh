#!/usr/bin/env bash
# Retire MAG Caddy only after external owner verification.
set -euo pipefail
[[ $EUID == 0 ]] || exit 1
[[ -f /var/lib/mag-private-cutover/confirmed ]] ||
 { echo "Owner cutover not confirmed; Caddy removal denied" >&2; exit 1; }
! systemctl is-active --quiet mag-private-rollback.timer ||
 { echo "Rollback watchdog armed" >&2; exit 1; }
nginx -t
systemctl is-active --quiet nginx.service
systemctl is-active --quiet mag-120.service
grep -F 'proxy_pass http://127.0.0.1:8766/_internal/tailscale/whois;' /etc/nginx/sites-available/mag-room.conf >/dev/null
systemctl disable --now mag-caddy.service 2>/dev/null || true
rm -f /etc/systemd/system/mag-caddy.service
rm -rf /etc/mag-caddy
if systemctl list-units --state=active --no-legend 'caddy*.service' 'mag-caddy*.service' | grep . >/dev/null; then
 echo "Other active Caddy service: binary retained for review" >&2
else
 rm -f /usr/local/bin/caddy
fi
systemctl daemon-reload
echo "MAG Caddy retired; mag-llm untouched."
