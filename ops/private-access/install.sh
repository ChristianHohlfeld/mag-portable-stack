#!/bin/bash
# Installs config/units. Binaries: lego (github.com/go-acme/lego release, sha256 verified)
# and caddy (github.com/caddyserver/caddy release, sha512 verified) into /usr/local/bin.
# Secrets: /etc/mag-acme/acme-dns.json must already exist (root 0600).
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
id caddy >/dev/null 2>&1 || useradd --system --home /var/lib/caddy --create-home --shell /usr/sbin/nologin caddy
install -d -m 0700 /etc/mag-acme
install -m 0644 "$here/env.conf" /etc/mag-acme/lego.env
install -d /usr/local/lib/mag-acme /etc/mag-caddy
install -m 0755 "$here/issue-cert.sh" "$here/deploy-hook.sh" /usr/local/lib/mag-acme/
install -m 0644 "$here/Caddyfile" /etc/mag-caddy/Caddyfile
install -m 0644 "$here"/mag-caddy.service "$here"/mag-acme-renew.service "$here"/mag-acme-renew.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now mag-acme-renew.timer
echo "Now: issue-cert.sh staging, issue-cert.sh production, systemctl enable --now mag-caddy"
