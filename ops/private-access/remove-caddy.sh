#!/usr/bin/env bash
# One-time retirement of Caddy AFTER nginx guest/owner/fail-closed checks pass.
# Never stops or restarts mag-llm, mag-120 or the LLM process.
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "sudo required" >&2; exit 1; }
nginx -t
systemctl is-active --quiet nginx.service || { echo "nginx not active" >&2; exit 1; }
systemctl is-active --quiet mag-whois.service || { echo "mag-whois not active" >&2; exit 1; }
grep -Fq 'auth_request /_mag_whois;' /etc/nginx/sites-available/mag-room.conf || {
 echo "Fail-closed nginx auth_request missing"; exit 1;
}
echo "nginx/whois verified; retiring legacy Caddy ingress only"
systemctl disable --now mag-caddy.service 2>/dev/null || true
rm -f /etc/systemd/system/mag-caddy.service
rm -f /usr/local/bin/caddy
rm -rf /etc/mag-caddy
systemctl daemon-reload
echo "Legacy Caddy removed. nginx unchanged. mag-llm untouched."
