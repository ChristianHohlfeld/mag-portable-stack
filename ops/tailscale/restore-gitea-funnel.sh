#!/usr/bin/env bash
# Put back the serve config and nginx files saved by apply-gitea-funnel.sh.
# Releases tailscaled :443 before nginx reloads, so the room can bind it again.
# Does not restart a model or the room. Does not print certificate material.
# HTTPS↔TCP on :443 needs an empty port in between (tailscaled 1.104.1).
set +x
set -euo pipefail
txn=${MAG_NGINX_TXN:?}
snap=$txn/funnel-snapshot
[[ -d $snap && ! -L $snap ]] || exit 0
here=$(cd -- "$(dirname -- "$0")" && pwd)
merge=$here/merge-gitea-funnel.py
sock=${MAG_GITEA_FUNNEL_SOCK:-/var/run/tailscale/tailscaled.sock}
store=${MAG_GITEA_FUNNEL_SERVE:-}

post_json() {
  local src=$1 code=0 http out=$snap/post.body
  if [[ -n $store ]]; then
    if msg=$(python3 "$merge" needs-transition "$store" "$src"); then
      echo "funnel restore: serve-config POST HTTP 500" >&2
      echo "funnel restore: serve-config POST body: ${msg}" >&2
      return 1
    fi
    cp -a "$src" "$store"
    return 0
  fi
  command -v curl >/dev/null || return 1
  http=$(curl -sS --unix-socket "$sock" -o "$out" -w '%{http_code}' -X POST \
    -H 'Content-Type: application/json' --data-binary @"$src" \
    http://local-tailscaled.sock/localapi/v0/serve-config) || code=$?
  if (( code != 0 )); then
    echo "funnel restore: serve-config POST curl exit ${code}" >&2
    return 1
  fi
  if [[ $http != 200 ]]; then
    echo "funnel restore: serve-config POST HTTP ${http}" >&2
    if [[ -s $out ]]; then
      echo "funnel restore: serve-config POST body:" >&2
      cat -- "$out" >&2
    fi
    return 1
  fi
  return 0
}

if [[ -s $snap/serve.json ]] && { [[ -n $store ]] || [[ -S $sock ]]; }; then
  live=$snap/live.json
  trans=$snap/transition.json
  if [[ -n $store && -f $store ]]; then
    cp -a "$store" "$live"
  elif [[ -S $sock ]] && command -v curl >/dev/null; then
    curl -sS --unix-socket "$sock" -o "$live" \
      http://local-tailscaled.sock/localapi/v0/serve-config || true
  fi
  src=$snap/serve.json
  [[ -s $live ]] && src=$live
  python3 "$merge" transition <"$src" >"$trans" || true
  if [[ -s $trans ]]; then
    post_json "$trans" || true
  fi
  post_json "$snap/serve.json" || true
fi
if [[ -s $snap/manifest ]]; then
  while IFS=$'\t' read -r id file kind; do
    [[ -n ${id:-} && -n ${file:-} && -n ${kind:-} ]] || continue
    case "$file" in
      /etc/nginx/*|/etc/mag-ci/*) ;;
      *) echo 'funnel restore: refusing path' >&2; exit 1 ;;
    esac
    if [[ $kind == file ]]; then
      [[ -f $snap/files/$id ]] || { echo 'funnel restore: snapshot missing' >&2; exit 1; }
      cp -a "$snap/files/$id" "$file"
    elif [[ $kind == link ]]; then
      [[ -s $snap/files/$id ]] || { echo 'funnel restore: symlink snapshot missing' >&2; exit 1; }
      rm -f -- "$file"
      ln -s -- "$(cat "$snap/files/$id")" "$file"
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
