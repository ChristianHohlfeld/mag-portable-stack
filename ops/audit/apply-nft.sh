#!/usr/bin/env bash
# Renders ops/audit/mag-audit.nft for this host (the uids of mag-audit and
# magbot, the VM bridge from /etc/mag-audit/vmnet.env) and loads it when it
# changed. Root; idempotent; called by deploy.sh and install-audit.sh.
set -euo pipefail
[[ $EUID == 0 ]] || { echo 'root required' >&2; exit 1; }
here=$(cd -- "$(dirname -- "$0")" && pwd)
env_file=${MAG_AUDIT_VMNET:-/etc/mag-audit/vmnet.env}
[[ -r $env_file ]] || { echo "apply-nft: $env_file missing (ops/audit/detect-vm-net.sh)" >&2; exit 1; }
# shellcheck disable=SC1090
source "$env_file"
: "${VM_IFACES:?}" "${VM_SUBNET:?}"
tmp=$(mktemp)
trap 'rm -f "$tmp"' EXIT
sed -e "s/@UID_MAG_AUDIT@/$(id -u mag-audit)/g" -e "s/@UID_MAGBOT@/$(id -u magbot)/g" \
    -e "s|@VM_IFACES@|$VM_IFACES|g" -e "s|@VM_SUBNET@|$VM_SUBNET|g" \
    "$here/mag-audit.nft" > "$tmp"
target=/etc/nftables.d/mag-audit.nft
cmp -s "$tmp" "$target" && exit 0
nft -c -f "$tmp"
install -D -o root -g root -m 0640 "$tmp" "$target"
# Reload re-adds the Tailscale API exceptions (drop-in 40-tailscale-api-egress).
if systemctl is-active --quiet mag-audit-nft.service; then systemctl reload mag-audit-nft.service; fi
echo "apply-nft: $target updated"
