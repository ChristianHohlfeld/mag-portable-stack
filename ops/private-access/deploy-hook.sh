#!/bin/bash
# Copies the production cert where Caddy can read it, then restarts Caddy.
set -euo pipefail
. /etc/mag-acme/lego.env
src=/etc/mag-acme/lego/certificates
dst=/etc/mag-caddy/tls
install -d -m 0750 -o root -g caddy "$dst"
install -m 0644 -o root -g caddy "$src/$MAG_ACME_DOMAIN.crt" "$dst/cert.pem"
install -m 0640 -o root -g caddy "$src/$MAG_ACME_DOMAIN.key" "$dst/key.pem"
systemctl try-restart mag-caddy.service || true
