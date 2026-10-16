#!/bin/bash
# Installs config/units. Binaries: lego (github.com/go-acme/lego release, sha256 verified)
# and caddy (github.com/caddyserver/caddy release, sha512 verified) into /usr/local/bin.
# Secrets: /etc/mag-acme/acme-dns.json must already exist (root 0600).
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
id caddy >/dev/null 2>&1 || useradd --system --home /var/lib/caddy --create-home --shell /usr/sbin/nologin caddy
install -d -m 0700 /etc/mag-acme
install -m 0644 "$here/env.conf" /etc/mag-acme/lego.env
install -d /usr/local/lib/mag-acme /usr/local/lib/mag-private /etc/mag-caddy
install -m 0755 "$here/issue-cert.sh" "$here/deploy-hook.sh" /usr/local/lib/mag-acme/
install -m 0755 "$here/tailscale-whois.py" /usr/local/lib/mag-private/tailscale-whois.py
install -m 0644 "$here/access.json.example" /usr/local/lib/mag-private/access.json.example
install -m 0644 "$here/Caddyfile" /etc/mag-caddy/Caddyfile
install -m 0644 "$here"/mag-caddy.service "$here"/mag-whois.service "$here"/mag-acme-renew.service "$here"/mag-acme-renew.timer /etc/systemd/system/
# Owner login lives in /var/lib/magbot/room/access.json (not this repo). Never overwrite it.
systemctl daemon-reload
systemctl enable --now mag-acme-renew.timer
if systemctl enable --now mag-whois.service; then
  systemctl try-restart mag-caddy.service || true
else
  echo "mag-whois did not start; mag-caddy was NOT restarted" >&2
  exit 1
fi
echo "Whois helper is on 127.0.0.1:8765. mag-llm was not touched."
echo "Before guests connect, write /var/lib/magbot/room/access.json (see access.json.example)."
echo "Certs, if still needed: issue-cert.sh staging, issue-cert.sh production, systemctl enable --now mag-caddy"
