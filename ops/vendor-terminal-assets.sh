#!/usr/bin/env bash
# Vendor pinned xterm.js assets into MAG releases. No runtime CDN dependency.
set -euo pipefail
DST="$1"
test -n "$DST"
mkdir -p "$DST"
BASE="https://cdn.jsdelivr.net/npm"
download() {
  local url="$1" dest="$2" minimum="$3" tmp
  if [[ -s "$DST/$dest" ]] && [[ $(wc -c < "$DST/$dest") -ge "$minimum" ]]; then return; fi
  tmp="$(mktemp "$DST/.$dest.XXXXXX")"
  trap 'rm -f "$tmp"' EXIT
  curl --fail --silent --show-error --location --retry 2 --retry-delay 1 \
    --connect-timeout 8 --max-time 35 --proto '=https' --proto-redir '=https' \
    "$url" -o "$tmp"
  [[ $(wc -c < "$tmp") -ge "$minimum" ]] || { echo "Terminal asset too small: $url" >&2; exit 1; }
  chmod 0644 "$tmp"
  mv -f "$tmp" "$DST/$dest"
  trap - EXIT
}
download "$BASE/@xterm/xterm@5.5.0/lib/xterm.js" "xterm.js" 100000
download "$BASE/@xterm/xterm@5.5.0/css/xterm.css" "xterm.css" 1000
download "$BASE/@xterm/addon-fit@0.10.0/lib/addon-fit.js" "xterm-addon-fit.js" 500
