#!/usr/bin/env bash
# Render-only helper, no daemon, no privilege requirement; suitable for CI.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
. "${MAG_PAGES_CONFIG:-/etc/mag-pages/pages.env}"
: "${PAGES_PUBLIC_BIND:=}"
: "${PAGES_PUBLIC_DNS:=}"
: "${PAGES_TAILNET_BIND:=100.123.114.16}"
[[ "$PAGES_ZONE" =~ ^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$ && "$PAGES_ZONE" == *.* && "$PAGES_ZONE" != *".."* ]] || { echo 'Invalid Pages DNS zone' >&2; exit 2; }
valid_ip() {
  local i octet
  [[ "$1" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]] || return 1
  IFS=. read -r -a octets <<<"$1"
  for octet in "${octets[@]}"; do ((10#$octet <= 255)) || return 1; done
}
valid_ip "$PAGES_TAILNET_BIND" || { echo 'Invalid Tailnet IPv4' >&2; exit 2; }
if [[ -n "$PAGES_PUBLIC_BIND" ]]; then
  valid_ip "$PAGES_PUBLIC_BIND" || { echo 'Invalid Public IPv4' >&2; exit 2; }
  [[ "$PAGES_PUBLIC_BIND" != "$PAGES_TAILNET_BIND" ]] || { echo 'Public IPv4 cannot be Tailnet IPv4' >&2; exit 2; }
else
  [[ -z "$PAGES_PUBLIC_DNS" ]] || { echo 'PAGES_PUBLIC_DNS requires PAGES_PUBLIC_BIND' >&2; exit 2; }
fi
if [[ -n "$PAGES_PUBLIC_DNS" ]]; then valid_ip "$PAGES_PUBLIC_DNS" || exit 2; fi
# The following sed eats one backslash in the replacement, so the variable
# must contain a doubled backslash to leave `\.` in the nginx regex.
# PAGES_TAILNET_BIND stays the client-facing address (DNS, tailscale0). The
# template must not contain @TAILNET_BIND@: nginx listens on 127.0.0.1:4443
# and tailscaled owns the Tailscale :443.
zone_regex="$(printf %s "$PAGES_ZONE" | sed 's/[.]/\\\\./g')"
if grep -q '@TAILNET_BIND@' "$here/mag-pages.nginx.in"; then
  echo 'Pages template must not bind @TAILNET_BIND@; tailscaled owns :443' >&2
  exit 2
fi
sed -e "s|@ZONE@|$PAGES_ZONE|g" \
    -e "s|@ZONE_REGEX@|$zone_regex|g" "$here/mag-pages.nginx.in"
if [[ -n "$PAGES_PUBLIC_BIND" ]]; then
  sed -e "s|@PUBLIC_BIND@|$PAGES_PUBLIC_BIND|g" \
      -e "s|@ZONE_REGEX@|$zone_regex|g" "$here/mag-pages-public.nginx.in"
fi
