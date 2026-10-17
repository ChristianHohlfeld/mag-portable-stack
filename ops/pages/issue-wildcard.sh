#!/usr/bin/env bash
# Existing MAG lego/acme-dns credential store stays on the host.
set -euo pipefail
test "$EUID" -eq 0 || { echo 'Run with sudo' >&2; exit 1; }
mode="$1"
[[ "$mode" = run || "$mode" = renew ]] || { echo 'usage: issue-wildcard.sh run|renew' >&2; exit 2; }
test -s /etc/mag-acme/lego.env || { echo 'Install existing acme-dns credentials first' >&2; exit 1; }
. /etc/mag-acme/lego.env
export ACME_DNS_API_BASE ACME_DNS_STORAGE_PATH
domain="$(python3 -c 'import json;print(json.load(open("/etc/mag-pages/config.json"))["pages_domain"])')"
[[ "$domain" =~ ^[a-z0-9.-]+$ ]] || exit 1
test -x /usr/local/bin/lego || { echo 'lego missing' >&2; exit 1; }
mkdir -p /etc/mag-pages/tls /etc/mag-acme/lego-pages
chmod 0750 /etc/mag-pages/tls
if [[ "$mode" = renew ]]; then
    /usr/local/bin/lego --accept-tos --path /etc/mag-acme/lego-pages --dns acmedns --dns.resolvers 8.8.8.8:53,1.1.1.1:53 --domains "*.$domain" renew --days 30 --no-random-sleep
else
    /usr/local/bin/lego --accept-tos --path /etc/mag-acme/lego-pages --dns acmedns --dns.resolvers 8.8.8.8:53,1.1.1.1:53 --domains "*.$domain" run
fi
cert_dir=/etc/mag-acme/lego-pages/certificates
found=
for cert in "$cert_dir"/*.crt; do
    [[ -f "$cert" ]] || continue
    if openssl x509 -in "$cert" -noout -text | grep -Fq "DNS:*.$domain"; then
        found="$cert"
        break
    fi
done
[[ -n "$found" ]] || { echo 'Wildcard certificate not issued. Add _acme-challenge DNS delegation, see docs.' >&2; exit 1; }
key="$(printf %s "$found" | sed 's/\.crt$/.key/')"
test -s "$key"
openssl x509 -in "$found" -noout -checkend 86400 >/dev/null
openssl pkey -in "$key" -noout >/dev/null
install -m 0644 "$found" /etc/mag-pages/tls/wildcard.crt.next
install -m 0640 -g www-data "$key" /etc/mag-pages/tls/wildcard.key.next
cert_pub="$(openssl x509 -in /etc/mag-pages/tls/wildcard.crt.next -pubkey -noout | openssl sha256)"
key_pub="$(openssl pkey -in /etc/mag-pages/tls/wildcard.key.next -pubout | openssl sha256)"
[[ "$cert_pub" = "$key_pub" ]] || { echo 'Certificate/key mismatch' >&2; exit 1; }
mv -f /etc/mag-pages/tls/wildcard.crt.next /etc/mag-pages/tls/wildcard.crt
mv -f /etc/mag-pages/tls/wildcard.key.next /etc/mag-pages/tls/wildcard.key
if test -e /etc/nginx/conf.d/mag-pages-generated.conf; then
    nginx -t
    systemctl reload nginx.service
fi
echo "MAG Pages wildcard TLS ready; mag-llm untouched"
