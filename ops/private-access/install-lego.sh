#!/usr/bin/env bash
# Pinned lego (ACME DNS-01 client used by issue-cert.sh, ops/pages and ops/bots).
# Installs /usr/local/bin/lego from the GitHub release, SHA256-verified.
# Idempotent: an installed binary of the pinned version is left alone.
#   sudo bash ops/private-access/install-lego.sh [--dry-run]
set -euo pipefail
LEGO_VERSION=5.5.2
LEGO_ASSET=lego_v${LEGO_VERSION}_linux_amd64.tar.gz
LEGO_URL=https://github.com/go-acme/lego/releases/download/v${LEGO_VERSION}/${LEGO_ASSET}
# From lego_5.5.2_checksums.txt of the v5.5.2 release
LEGO_SHA256=2a35505089e7772c92e1e9ac144df91151ef2eca8568630db0ff91fca06d9bef
DEST=${MAG_LEGO_DEST:-/usr/local/bin/lego}
if [[ ${1:-} == --dry-run ]]; then
  echo "+ install lego $LEGO_VERSION ($LEGO_SHA256) to $DEST when absent or a different version"
  exit 0
fi
[[ $EUID == 0 || -n ${MAG_LEGO_DEST:-} ]] || { echo 'root required' >&2; exit 1; }
[[ $(uname -m) == x86_64 ]] || { echo 'lego pin is linux amd64 only' >&2; exit 1; }
if [[ -x $DEST ]] && "$DEST" --version 2>/dev/null | grep -Fq "$LEGO_VERSION"; then
  echo "lego $LEGO_VERSION already installed"
  exit 0
fi
tmp=$(mktemp -d /var/tmp/mag-lego.XXXXXXXX)
trap 'rm -rf -- "$tmp"' EXIT
curl --proto '=https' --tlsv1.2 -fsSL --retry 3 --max-time 300 "$LEGO_URL" -o "$tmp/lego.tgz"
printf '%s  %s\n' "$LEGO_SHA256" "$tmp/lego.tgz" | sha256sum -c --quiet - ||
  { echo 'lego SHA256 mismatch; not installed' >&2; exit 1; }
tar -xzf "$tmp/lego.tgz" -C "$tmp" lego
"$tmp/lego" --version | grep -Fq "$LEGO_VERSION" || { echo 'unexpected lego version in archive' >&2; exit 1; }
install -m 0755 "$tmp/lego" "$DEST.next"
mv -f "$DEST.next" "$DEST"
echo "lego $LEGO_VERSION installed to $DEST"
