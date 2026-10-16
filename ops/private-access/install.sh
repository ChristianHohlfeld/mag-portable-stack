#!/usr/bin/env bash
# Install nginx Tailscale whois gateway. The model process is never touched.
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "Run with sudo" >&2; exit 1; }
here=$(cd "$(dirname "$0")" && pwd)
command -v nginx >/dev/null || { echo "nginx missing (apt install nginx)" >&2; exit 1; }
install -d -m 0700 /etc/mag-acme
install -m 0644 "$here/env.conf" /etc/mag-acme/lego.env
install -d -m 0755 /usr/local/lib/mag-acme /usr/local/lib/mag-private /etc/nginx/sites-available /etc/nginx/sites-enabled
install -m 0755 "$here/issue-cert.sh" "$here/deploy-hook.sh" /usr/local/lib/mag-acme/
install -m 0755 "$here/tailscale-whois.py" /usr/local/lib/mag-private/tailscale-whois.py
install -m 0644 "$here/access.json.example" /usr/local/lib/mag-private/access.json.example
install -m 0644 "$here"/mag-whois.service "$here"/mag-acme-renew.service "$here"/mag-acme-renew.timer /etc/systemd/system/
# Preserve Ali's previous nginx config for rollback. Do not overwrite it later.
if [[ -f /etc/nginx/sites-available/mag-room.conf && ! -e /etc/nginx/sites-available/mag-room.conf.pre-whois ]]; then
 cp -a /etc/nginx/sites-available/mag-room.conf /etc/nginx/sites-available/mag-room.conf.pre-whois
fi
install -m 0644 "$here/mag-room.nginx.conf" /etc/nginx/sites-available/mag-room.conf
ln -sfn ../sites-available/mag-room.conf /etc/nginx/sites-enabled/mag-room.conf
systemctl daemon-reload
systemctl enable --now mag-whois.service
systemctl is-active --quiet mag-whois.service || { echo "mag-whois unavailable – nginx not reloaded" >&2; exit 1; }
# TLS certs must exist before switching the active virtual host.
test -s /etc/mag-nginx/tls/cert.pem && test -s /etc/mag-nginx/tls/key.pem || {
 echo "Missing /etc/mag-nginx/tls certificates. Issue/migrate cert first; nginx not reloaded" >&2; exit 1;
}
nginx -t || { echo "nginx config test failed – not reloaded" >&2; exit 1; }
systemctl reload nginx.service
systemctl enable --now mag-acme-renew.timer
echo "nginx reloaded. mag-whois on 127.0.0.1:8765. mag-llm untouched."
