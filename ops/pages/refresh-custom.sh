#!/usr/bin/env bash
# Existing MAG ACME timer hook; lego v5 run for all issuance and renewal.
set -euo pipefail
[[ $EUID == 0 ]] || exit 1
[[ -f /etc/mag-pages/pages.env ]] || exit 0
. /etc/mag-pages/pages.env
: "${PAGES_PUBLIC_BIND:=}"
mode="${1:-run}"
[[ "$mode" == run || "$mode" == custom-only ]] || { echo 'Usage: refresh-custom.sh run|custom-only' >&2; exit 2; }
config=/etc/nginx/conf.d/mag-pages-custom.conf
# Private-by-default: NEVER issue HTTP-01 or retain public custom vhosts.
if [[ -z "$PAGES_PUBLIC_BIND" ]]; then
  if [[ -e "$config" ]]; then
    old="$(mktemp /etc/nginx/conf.d/.pages-old.XXXXXXXX)"
    trap 'rm -f "$old"' EXIT
    cp -a "$config" "$old"
    rm -f "$config"
    if ! nginx -t || ! systemctl reload nginx.service; then
      cp -a "$old" "$config"; nginx -t && systemctl reload nginx.service || true
      exit 1
    fi
  fi
  echo 'Custom HTTPS disabled: PAGES_PUBLIC_BIND empty (Tailnet-only)'
  if [[ "$mode" == run ]]; then /usr/local/lib/mag-pages/issue-wildcard.sh run; fi
  exit 0
fi
if [[ "$mode" == run ]]; then /usr/local/lib/mag-pages/issue-wildcard.sh run; fi
[[ "$PAGES_PUBLIC_BIND" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]] || exit 2
[[ "$PAGES_ACME_EMAIL" =~ ^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+$ && "$PAGES_ACME_EMAIL" != operator@example.com ]] || exit 2
manifest=/var/lib/mag-pages/custom-domains.tsv
[[ -r "$manifest" ]] || exit 0
temp="$(mktemp /etc/nginx/conf.d/.pages-custom.XXXXXXXX)"
prior="$(mktemp /etc/nginx/conf.d/.pages-prior.XXXXXXXX)"
seen="$(mktemp)"
failed=0
trap 'rm -f "$temp" "$prior" "$seen"' EXIT
[[ ! -f "$config" ]] || cp -a "$config" "$prior"
: >"$temp"
while IFS=$'\t' read -r host canonical slug; do
 [[ -n "$host" ]] || continue
 [[ "$host" =~ ^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$ && "$host" == *.* &&
    "$host" != *".."* && "$canonical" =~ ^[a-z0-9.-]+$ &&
    "$slug" =~ ^[a-z0-9-]+$ && "$canonical" == "$slug.$PAGES_ZONE" ]] || exit 1
 if grep -Fxq "$host" "$seen"; then echo 'Duplicate custom hostname' >&2; exit 1; fi
 echo "$host" >> "$seen"
 actual="$(dig +short CNAME "$host" @1.1.1.1 | sed 's/[.]$//' | tr '[:upper:]' '[:lower:]')"
 if [[ "$actual" != "$canonical" ]]; then
   echo "DNS not ready for $host: CNAME must point to $canonical" >&2
   failed=1
   continue
 fi
 id="$(printf '%s' "$host" | sha256sum | cut -c1-16)"
 dir="/etc/mag-pages/tls/custom/$id"
 mkdir -p "$dir"
 chmod 0700 "$dir"
 cert="$dir/certificates/$host.crt"
 key="$dir/certificates/$host.key"
 # lego 5: run is idempotent; there is NO renew subcommand.
 if ! /usr/local/bin/lego run --accept-tos --email "$PAGES_ACME_EMAIL" \
     --path "$dir" --http --http.webroot /var/lib/mag-pages/acme -d "$host"; then
    echo "Custom-domain lego run failed: $host" >&2
    failed=1
    continue
 fi
 [[ -s "$cert" && -s "$key" ]] || { failed=1; continue; }
 openssl x509 -in "$cert" -noout -checkend 86400 >/dev/null || { failed=1; continue; }
 [[ "$(openssl x509 -in "$cert" -pubkey -noout | openssl sha256)" == \
    "$(openssl pkey -in "$key" -pubout | openssl sha256)" ]] || { failed=1; continue; }
 cat >> "$temp" <<EOF
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
done <"$manifest"
if [[ -s "$temp" ]]; then
  install -m 0644 "$temp" "$config"
else
  rm -f "$config" # no stale empty custom-domain snippet
fi
if ! nginx -t || ! systemctl reload nginx.service; then
 if [[ -s "$prior" ]]; then cp -a "$prior" "$config"; else rm -f "$config"; fi
 nginx -t && systemctl reload nginx.service || true
 echo 'Custom vhost rejected; restored prior config' >&2
 exit 1
fi

if [[ "$failed" -ne 0 ]]; then
 echo 'Custom-domain certificate/DNS activation incomplete; will retry via existing ACME timer' >&2
 exit 1
fi
