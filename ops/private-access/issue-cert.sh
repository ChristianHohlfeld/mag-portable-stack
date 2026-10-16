#!/bin/bash
# Usage: issue-cert.sh staging|production|renew
set -euo pipefail
. /etc/mag-acme/lego.env
export ACME_DNS_API_BASE ACME_DNS_STORAGE_PATH
mode=${1:-renew}
case "$mode" in
  staging) server=https://acme-staging-v02.api.letsencrypt.org/directory; path=/etc/mag-acme/lego-staging ;;
  production|renew) server=https://acme-v02.api.letsencrypt.org/directory; path=/etc/mag-acme/lego ;;
  *) echo "usage: $0 staging|production|renew" >&2; exit 2 ;;
esac
extra=()
[ "$mode" = renew ] || extra+=(--no-random-sleep)
hook=()
[ "$mode" = staging ] || hook=(--deploy-hook /usr/local/lib/mag-acme/deploy-hook.sh)
exec /usr/local/bin/lego run --accept-tos --server "$server" --path "$path" \
  --dns acmedns --dns.resolvers 8.8.8.8:53,1.1.1.1:53 \
  -d "$MAG_ACME_DOMAIN" "${extra[@]}" "${hook[@]}"
