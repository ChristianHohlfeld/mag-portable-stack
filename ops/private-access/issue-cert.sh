#!/usr/bin/env bash
# ACME DNS-01 using lego v5.5.2: flags belong to the 'run' subcommand.
# 'run' obtains AND renews; never restarts the model or room.
set -euo pipefail
[[ $EUID == 0 ]] || { echo "root required" >&2; exit 1; }
[[ $# -le 1 ]] || { echo "Usage: $0 staging|production|renew" >&2; exit 2; }
. /etc/mag-acme/lego.env
[[ ${MAG_ACME_DOMAIN:-} == mag.stardenburg.de ]] || { echo "Unexpected ACME domain" >&2; exit 1; }
: "${ACME_DNS_API_BASE:?missing ACME DNS endpoint}"
: "${ACME_DNS_STORAGE_PATH:?missing ACME DNS state}"
export ACME_DNS_API_BASE ACME_DNS_STORAGE_PATH
mode=${1:-renew}
case "$mode" in
  staging)
    server=https://acme-staging-v02.api.letsencrypt.org/directory
    path=/etc/mag-acme/lego-staging
    ;;
  production|renew)
    server=https://acme-v02.api.letsencrypt.org/directory
    path=/etc/mag-acme/lego
    ;;
  *) echo "Usage: $0 staging|production|renew" >&2; exit 2 ;;
esac
args=(--accept-tos --server "$server" --path "$path"
      --dns acmedns --dns.resolvers 8.8.8.8:53,1.1.1.1:53
      -d "$MAG_ACME_DOMAIN")
if [[ -n ${MAG_ACME_EMAIL:-} ]]; then args+=(--email "$MAG_ACME_EMAIL"); fi
[[ $mode == renew ]] || args+=(--no-random-sleep)
[[ $mode == staging ]] || args+=(--deploy-hook /usr/local/lib/mag-acme/deploy-hook.sh)
exec /usr/local/bin/lego run "${args[@]}"
