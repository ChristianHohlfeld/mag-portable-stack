#!/usr/bin/env bash
# Only pages/nginx/timers. Requires pre-provisioned wildcard TLS and local Gitea token.
set -euo pipefail
test "$EUID" -eq 0 || { echo 'Run as root' >&2; exit 1; }
src="$(cd -- "$(dirname -- "$0")" && pwd)"
command -v nginx >/dev/null
command -v certbot >/dev/null
command -v dig >/dev/null
command -v python3 >/dev/null
test -s /etc/mag-pages/config.json || { echo 'Copy and edit config.example.json to /etc/mag-pages/config.json' >&2; exit 1; }
test -s /etc/mag-pages/gitea-token || { echo 'Add Gitea read-only access token to /etc/mag-pages/gitea-token (0600)' >&2; exit 1; }
test -s /etc/mag-pages/tls/wildcard.crt
test -s /etc/mag-pages/tls/wildcard.key
nginx -t
# Fail before any nginx changes if the configured bind IPs are not on MAG.
for ip in $(python3 -c 'import json; c=json.load(open("/etc/mag-pages/config.json")); print(c["public_ip"], c["tailnet_ip"])'); do
    ip -4 -o addr show | grep -Fq " $ip/" || { echo "Configured bind IP not present on mag: $ip" >&2; exit 1; }
done
install -d -m 0755 /usr/local/lib/mag-pages /var/lib/mag-pages/releases /var/lib/mag-pages/acme
install -d -m 0700 /etc/mag-pages
install -m 0755 "$src/mag_pages.py" "$src/issue-wildcard.sh" /usr/local/lib/mag-pages/
install -m 0644 "$src"/mag-pages-*.service "$src"/mag-pages-*.timer /etc/systemd/system/
if [[ -e /etc/nginx/conf.d/mag-pages-generated.conf && ! -e /etc/nginx/conf.d/mag-pages-generated.conf.pre-pages ]]; then
    cp -a /etc/nginx/conf.d/mag-pages-generated.conf /etc/nginx/conf.d/mag-pages-generated.conf.pre-pages
fi
systemctl daemon-reload
systemctl start mag-pages-sync.service
systemctl enable --now mag-pages-sync.timer mag-pages-cert.timer
echo 'MAG Pages sync active; nginx reloaded after config validation; no mag-llm restart.'
