#!/usr/bin/env bash
# lego DNS-01: staging, production run, or renew (never model restart).
set -euo pipefail
. /etc/mag-acme/lego.env
[[ $MAG_ACME_DOMAIN == mag.stardenburg.de ]] || exit 1
export ACME_DNS_API_BASE ACME_DNS_STORAGE_PATH
mode=renew
if (( $# > 0 )); then mode=$1; fi
case "$mode" in
 staging)
  exec /usr/local/bin/lego --accept-tos --server https://acme-staging-v02.api.letsencrypt.org/directory \
    --path /etc/mag-acme/lego-staging --dns acmedns -d "$MAG_ACME_DOMAIN" run ;;
 production)
  exec /usr/local/bin/lego --accept-tos --server https://acme-v02.api.letsencrypt.org/directory \
    --path /etc/mag-acme/lego --dns acmedns -d "$MAG_ACME_DOMAIN" \
    --deploy-hook /usr/local/lib/mag-acme/deploy-hook.sh run ;;
 renew)
  # lego v5 removed 'renew'; 'run' now renews existing certs.
  # lego v4 retains 'renew'. Select by command list, fail closed on unknown.
  commands=$(/usr/local/bin/lego --help 2>&1)
  if grep -Eq '^[[:space:]]+renew[[:space:]]' <<< "$commands"; then action=renew
  else action=run; fi
  exec /usr/local/bin/lego --accept-tos --server https://acme-v02.api.letsencrypt.org/directory \
    --path /etc/mag-acme/lego --dns acmedns -d "$MAG_ACME_DOMAIN" \
    --deploy-hook /usr/local/lib/mag-acme/deploy-hook.sh "$action" ;;
 *) echo "Usage: $0 staging|production|renew" >&2; exit 2 ;;
esac
