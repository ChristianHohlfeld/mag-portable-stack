#!/usr/bin/env bash
# lego deploy hook. Only reload nginx after atomically installing a validated
# certificate. Never touch mag-llm or magroom.
set -euo pipefail
. /etc/mag-acme/lego.env
src=/etc/mag-acme/lego/certificates
dst=/etc/mag-nginx/tls
install -d -m 0750 -o root -g www-data "$dst"
install -m 0644 -o root -g www-data "$src/$MAG_ACME_DOMAIN.crt" "$dst/cert.pem.new"
install -m 0640 -o root -g www-data "$src/$MAG_ACME_DOMAIN.key" "$dst/key.pem.new"
openssl x509 -in "$dst/cert.pem.new" -noout -checkend 86400 >/dev/null
openssl pkey -in "$dst/key.pem.new" -noout >/dev/null
mv -f "$dst/cert.pem.new" "$dst/cert.pem"
mv -f "$dst/key.pem.new" "$dst/key.pem"
nginx -t
systemctl reload nginx.service
