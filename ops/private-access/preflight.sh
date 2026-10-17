#!/usr/bin/env bash
# Read-only safety check for MAG private nginx access. No service changes.
set -euo pipefail
command -v nginx >/dev/null
nginx -t
systemctl is-active --quiet nginx.service
systemctl is-active --quiet tailscaled.service
[[ -s /etc/nginx/sites-available/mag-room.conf ]] || { echo "Host nginx site missing" >&2; exit 1; }
[[ -s /etc/mag-acme/lego.env ]] || { echo "Host lego.env missing" >&2; exit 1; }
. /etc/mag-acme/lego.env
[[ $MAG_ACME_DOMAIN == mag.stardenburg.de ]] || { echo "ACME domain mismatch" >&2; exit 1; }
[[ -s /etc/mag-acme/acme-dns.json ]] || { echo "acme-dns state missing" >&2; exit 1; }
[[ -s /etc/mag-acme/lego/certificates/mag.stardenburg.de.crt &&
   -s /etc/mag-acme/lego/certificates/mag.stardenburg.de.key ]] ||
  { echo "lego production cert/key missing" >&2; exit 1; }
echo "Existing nginx vhost, tailscaled, and lego files found."
echo "Check current TLS paths (do NOT print key content):"
grep -E '^[[:space:]]*ssl_certificate(_key)?[[:space:]]' /etc/nginx/sites-available/mag-room.conf || true
echo "Check local Tailnet address includes 100.123.114.16:"
ip -4 addr show | grep -F '100.123.114.16/' >/dev/null ||
 { echo "Expected Tailnet IP not present; do not cut over" >&2; exit 1; }
echo "No changes made. Before cutover use: sudo bash ops/private-access/deploy-hook.sh --no-reload"
