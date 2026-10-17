#!/usr/bin/env bash
# lego DNS-01 hook. --no-reload stages certs before cutover.
set -euo pipefail
[[ $EUID == 0 ]] || exit 1
(( $# <= 1 )) || exit 2
if (( $# == 1 )); then [[ $1 == --no-reload ]] || exit 2; fi
. /etc/mag-acme/lego.env
[[ $MAG_ACME_DOMAIN == mag.stardenburg.de ]] || exit 1
src=/etc/mag-acme/lego/certificates
dst=/etc/mag-nginx/tls
install -d -m 0700 -o root -g root "$dst"
install -m 0644 -o root -g root "$src/$MAG_ACME_DOMAIN.crt" "$dst/cert.pem.new"
install -m 0600 -o root -g root "$src/$MAG_ACME_DOMAIN.key" "$dst/key.pem.new"
trap 'rm -f "$dst/cert.pem.new" "$dst/key.pem.new"' EXIT
openssl x509 -in "$dst/cert.pem.new" -checkend 86400 -noout >/dev/null
openssl x509 -in "$dst/cert.pem.new" -checkhost "$MAG_ACME_DOMAIN" -noout | grep -F 'does match' >/dev/null
a=$(openssl x509 -in "$dst/cert.pem.new" -pubkey -noout | openssl pkey -pubin -outform DER | sha256sum | cut -d' ' -f1)
b=$(openssl pkey -in "$dst/key.pem.new" -pubout -outform DER | sha256sum | cut -d' ' -f1)
[[ $a == "$b" ]] || { echo "TLS key mismatch" >&2; exit 1; }
mv -f "$dst/cert.pem.new" "$dst/cert.pem"
mv -f "$dst/key.pem.new" "$dst/key.pem"
trap - EXIT
if (( $# == 0 )); then
 nginx -t
 systemctl reload nginx.service
fi
