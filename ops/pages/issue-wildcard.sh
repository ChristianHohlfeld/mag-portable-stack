#!/usr/bin/env bash
# Reuses existing MAG lego and acme-dns, not a new daemon.
set -euo pipefail
[[ $EUID == 0 ]] || exit 1
. /etc/mag-pages/pages.env
. /etc/mag-acme/lego.env
export ACME_DNS_API_BASE ACME_DNS_STORAGE_PATH
[[ "$PAGES_ZONE" =~ ^[a-z0-9.-]+$ ]] || exit 1
mode="$1"
[[ "$mode" == run || "$mode" == renew ]] || exit 2
base=/etc/mag-acme/lego-pages
mkdir -p "$base" /etc/mag-pages/tls/releases
if [[ "$mode" == renew ]]; then
 /usr/local/bin/lego --accept-tos --path "$base" --dns acmedns --dns.resolvers 8.8.8.8:53,1.1.1.1:53 -d "*.$PAGES_ZONE" renew --days 30 --no-random-sleep
else
 /usr/local/bin/lego --accept-tos --path "$base" --dns acmedns --dns.resolvers 8.8.8.8:53,1.1.1.1:53 -d "*.$PAGES_ZONE" run
fi
crt=
for file in "$base"/certificates/*.crt; do
 [[ -f "$file" ]] || continue
 if openssl x509 -in "$file" -noout -ext subjectAltName | grep -Fq "DNS:*.$PAGES_ZONE"; then crt="$file"; break; fi
done
[[ -n "$crt" && -s "$crt" ]] || { echo 'Wildcard TLS unavailable: configure acme-dns CNAME' >&2; exit 1; }
key="$(echo "$crt" | sed 's/[.]crt$/.key/')"
[[ -s "$key" ]] || exit 1
hash="$(openssl x509 -in "$crt" -outform DER | sha256sum | cut -c1-16)"
dest="/etc/mag-pages/tls/releases/$hash"
install -d -m 0750 -o root -g www-data "$dest"
install -m 0644 "$crt" "$dest/cert.pem"
install -m 0640 -g www-data "$key" "$dest/key.pem"
openssl x509 -in "$dest/cert.pem" -noout -checkend 86400 >/dev/null
[[ "$(openssl x509 -in "$dest/cert.pem" -pubkey -noout | openssl sha256)" == \
   "$(openssl pkey -in "$dest/key.pem" -pubout | openssl sha256)" ]] || exit 1
prior="$(readlink /etc/mag-pages/tls/current 2>/dev/null || true)"
ln -sfn "$dest" /etc/mag-pages/tls/.next
mv -Tf /etc/mag-pages/tls/.next /etc/mag-pages/tls/current
if [[ -e /etc/nginx/conf.d/mag-pages.conf ]]; then
 if ! nginx -t || ! systemctl reload nginx.service; then
  if [[ -n "$prior" ]]; then ln -sfn "$prior" /etc/mag-pages/tls/.next; mv -Tf /etc/mag-pages/tls/.next /etc/mag-pages/tls/current
  else rm -f /etc/mag-pages/tls/current; fi
  echo 'nginx failed, previous TLS restored' >&2; exit 1
 fi
fi
