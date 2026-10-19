#!/usr/bin/env bash
# Public ISRG roots (X1, X2, and Generation Y Root YE / Root YR) into the host
# trust store, plus the guest package magbot audit-proxy reads. Idempotent.
# The read-only golden VM image is not mounted or changed. Guest copies live
# only on the bot overlay and disappear when that VM is despawned.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
[[ $EUID -eq 0 ]] || { echo 'install-trust: run as root' >&2; exit 1; }
bundle=$(mktemp)
trap 'rm -f "$bundle"' EXIT
cat "$here/isrg-root-x1.pem" "$here/isrg-root-x2.pem" \
    "$here/isrg-root-ye.pem" "$here/isrg-root-yr.pem" > "$bundle"
[[ -s $bundle ]] || { echo 'install-trust: ISRG roots missing' >&2; exit 1; }
dest=/usr/local/share/ca-certificates
install -d -m 0755 "$dest"
changed=0
# Drop anchors we previously installed, then write the current set. A smaller
# bundle must not leave a stale mag-isrg-NN.crt behind.
shopt -s nullglob
for old in "$dest"/mag-isrg-*.crt; do rm -f "$old"; changed=1; done
shopt -u nullglob
n=0
while IFS= read -r line; do
  if [[ $line == '-----BEGIN CERTIFICATE-----' ]]; then
    n=$((n + 1))
    out=$(printf '%s/mag-isrg-%02d.crt' "$dest" "$n")
    : > "$out"
  fi
  [[ $n -gt 0 ]] || continue
  printf '%s\n' "$line" >> "$out"
  if [[ $line == '-----END CERTIFICATE-----' ]]; then chmod 0644 "$out"; fi
done < "$bundle"
[[ $n -ge 1 ]] || { echo 'install-trust: no certificates in bundle' >&2; exit 1; }
changed=1
if [[ $changed -eq 1 ]]; then
  update-ca-certificates >/dev/null
fi
# mitmproxy's client bundle is a snapshot. Rebuild it so upstream checks see
# the same roots the system store just gained.
if [[ -f /etc/mag-audit/mitmproxy-ca-cert.pem && -f /etc/ssl/certs/ca-certificates.crt ]]; then
  cat /etc/ssl/certs/ca-certificates.crt /etc/mag-audit/mitmproxy-ca-cert.pem \
    > /etc/mag-audit/ca-bundle.pem
  chmod 0644 /etc/mag-audit/ca-bundle.pem
fi
if [[ -d /etc/mag-audit/vm ]]; then
  install -m 0644 "$bundle" /etc/mag-audit/vm/isrg-roots.pem
  setup=$here/../audit/vm-proxy-setup.sh
  [[ -f $setup ]] || setup=/opt/mag-stack/current/ops/audit/vm-proxy-setup.sh
  if [[ -f $setup ]]; then
    install -m 0755 "$setup" /etc/mag-audit/vm/vm-proxy-setup.sh
  fi
fi
echo "install-trust: $n ISRG roots in $dest"
