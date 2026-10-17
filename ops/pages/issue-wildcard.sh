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
# Stage both files in a versioned directory, then switch ONE symlink atomically.
revision="$(openssl x509 -in "$found" -outform DER | sha256sum | cut -c1-16)"
dest="/etc/mag-pages/tls/releases/$revision"
install -d -m 0750 -o root -g www-data "$dest"
install -m 0644 -o root -g www-data "$found" "$dest/cert.pem"
install -m 0640 -o root -g www-data "$key" "$dest/key.pem"
openssl x509 -in "$dest/cert.pem" -noout -checkend 86400 >/dev/null
cert_pub="$(openssl x509 -in "$dest/cert.pem" -pubkey -noout | openssl sha256)"
key_pub="$(openssl pkey -in "$dest/key.pem" -pubout | openssl sha256)"
[[ "$cert_pub" = "$key_pub" ]] || { echo 'Certificate/key mismatch' >&2; exit 1; }
current=/etc/mag-pages/tls/current
previous="$(readlink "$current" 2>/dev/null || true)"
restore_cert() {
    if [[ -n "$previous" ]]; then
        ln -sfn "$previous" /etc/mag-pages/tls/.current-next
        mv -Tf /etc/mag-pages/tls/.current-next "$current"
    else
        rm -f "$current"
    fi
}
ln -sfn "$dest" /etc/mag-pages/tls/.current-next
mv -Tf /etc/mag-pages/tls/.current-next "$current"
if test -e /etc/nginx/conf.d/mag-pages-generated.conf; then
    if ! nginx -t; then
        restore_cert
        echo 'New wildcard certificate failed nginx -t; previous certificate restored' >&2
        exit 1
    fi
    if ! systemctl reload nginx.service; then
        restore_cert
        nginx -t && systemctl reload nginx.service || true
        echo 'nginx reload failed; previous wildcard certificate restored' >&2
        exit 1
    fi
fi
echo "MAG Pages wildcard TLS ready; mag-llm untouched"
