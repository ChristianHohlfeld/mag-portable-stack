#!/usr/bin/env bash
# Root-only ACME reconciler attached to the pre-existing mag-acme-renew timer.
# All content publishing and link revocation happen in native C++ magroom.
set -euo pipefail
[[ $EUID == 0 ]] || exit 1
[[ -f /etc/mag-pages/pages.env ]] || exit 0
. /etc/mag-pages/pages.env
[[ "$PAGES_ZONE" =~ ^[a-z0-9.-]+$ ]] || exit 1
if [[ "${1:-}" != custom-only ]]; then
 /usr/local/lib/mag-pages/issue-wildcard.sh renew
fi
manifest=/var/lib/mag-pages/custom-domains.tsv
[[ -f "$manifest" ]] || exit 0
config=/etc/nginx/conf.d/mag-pages-custom.conf
tmp="$(mktemp /etc/nginx/conf.d/.mag-pages-custom.XXXXXXXX)"
prior="$(mktemp /etc/nginx/conf.d/.mag-pages-before.XXXXXXXX)"
trap 'rm -f "$tmp" "$prior"' EXIT
[[ -f "$config" ]] && cp -a "$config" "$prior"
: > "$tmp"
seen="$(mktemp)"
trap 'rm -f "$tmp" "$prior" "$seen"' EXIT
while IFS=$'\t' read -r host canonical slug; do
 [[ -n "$host" ]] || continue
 # Every interpolated value is validated; a repo CNAME is NOT trusted shell/config.
 [[ "$host" =~ ^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$ && "$host" == *.* &&
    "$host" != *".."* && "$canonical" =~ ^[a-z0-9.-]+$ &&
    "$slug" =~ ^[a-z0-9-]+$ && "$canonical" == "$slug.$PAGES_ZONE" ]] || exit 1
 if grep -Fxq "$host" "$seen"; then echo 'Duplicate Pages custom hostname' >&2; exit 1; fi
 echo "$host" >> "$seen"
 # Explicit CNAME ownership; apex A-only hijacks are intentionally disallowed.
 actual="$(dig +short CNAME "$host" @1.1.1.1 | sed 's/[.]$//' | tr '[:upper:]' '[:lower:]')"
 if [[ "$actual" != "$canonical" ]]; then
   echo "DNS pending for $host; expected CNAME $canonical" >&2
   continue
 fi
 id="$(printf %s "$host" | sha256sum | cut -c1-16)"
 dir="/etc/mag-pages/tls/custom/$id"
 mkdir -p "$dir"
 chmod 0700 "$dir"
 cert="$dir/certificates/$host.crt"
 key="$dir/certificates/$host.key"
 if [[ -s "$cert" ]]; then
   if ! openssl x509 -in "$cert" -noout -checkend 2592000 >/dev/null; then
      /usr/local/bin/lego --accept-tos --email "$PAGES_ACME_EMAIL" --path "$dir" --http --http.webroot /var/lib/mag-pages/acme -d "$host" renew --days 30 ||
        { echo "TLS renewal failed: $host" >&2; continue; }
   fi
 else
   /usr/local/bin/lego --accept-tos --email "$PAGES_ACME_EMAIL" --path "$dir" --http --http.webroot /var/lib/mag-pages/acme -d "$host" run ||
     { echo "TLS issuance failed: $host" >&2; continue; }
 fi
 [[ -s "$cert" && -s "$key" ]] || continue
 openssl x509 -in "$cert" -noout -checkend 86400 >/dev/null || continue
 [[ "$(openssl x509 -in "$cert" -pubkey -noout | openssl sha256)" == \
    "$(openssl pkey -in "$key" -pubout | openssl sha256)" ]] || continue
 # The symlink for a custom domain is C++-managed, removed immediately if
 # the operator reverts to Tailnet-only (even before nginx config is updated).
 cat >> "$tmp" <<EOF
server {
    listen $PAGES_PUBLIC_BIND:443 ssl;
    server_name $host;
    ssl_certificate $cert;
    ssl_certificate_key $key;
    root /var/lib/mag-pages/custom/\$host;
    index index.html;
    autoindex off;
    add_header X-Content-Type-Options nosniff always;
    location ~ (^|/)\\. { return 404; }
    location / { try_files \$uri \$uri/ =404; }
}
EOF
done < "$manifest"
install -m 0644 "$tmp" "$config"
if ! nginx -t || ! systemctl reload nginx.service; then
 if [[ -s "$prior" ]]; then cp -a "$prior" "$config"; else rm -f "$config"; fi
 nginx -t && systemctl reload nginx.service || true
 echo 'Custom Pages nginx config rejected; prior version restored' >&2; exit 1
fi
