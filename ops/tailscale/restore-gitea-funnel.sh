#!/usr/bin/env bash
# Put back the serve config and nginx files saved by apply-gitea-funnel.sh.
# Releases tailscaled :443 before nginx reloads, so the room can bind it again.
# Does not restart a model or the room. Does not print certificate material.
set +x
set -euo pipefail
txn=${MAG_NGINX_TXN:?}
snap=$txn/funnel-snapshot
[[ -d $snap && ! -L $snap ]] || exit 0
sock=/var/run/tailscale/tailscaled.sock
if [[ -S $sock && -s $snap/serve.json ]]; then
  curl -sS --unix-socket "$sock" -o /dev/null -X POST \
    -H 'Content-Type: application/json' --data-binary @"$snap/serve.json" \
    http://local-tailscaled.sock/localapi/v0/serve-config || true
fi
if [[ -s $snap/manifest ]]; then
  while IFS=$'\t' read -r id file kind; do
    [[ -n ${id:-} && -n ${file:-} && -n ${kind:-} ]] || continue
    case "$file" in
      /etc/nginx/*|/etc/mag-ci/gitea-proxy.inc) ;;
      *) echo 'funnel restore: refusing path' >&2; exit 1 ;;
    esac
    if [[ $kind == file ]]; then
      [[ -f $snap/files/$id ]] || { echo 'funnel restore: snapshot missing' >&2; exit 1; }
      cp -a "$snap/files/$id" "$file"
    elif [[ $kind == absent ]]; then
      rm -f -- "$file"
    else
      echo 'funnel restore: bad manifest' >&2
      exit 1
    fi
  done < "$snap/manifest"
fi
if command -v nginx >/dev/null && command -v systemctl >/dev/null; then
  nginx -t && systemctl reload nginx.service || true
fi
