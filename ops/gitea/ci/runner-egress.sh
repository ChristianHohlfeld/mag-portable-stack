#!/usr/bin/env bash
# Fixed root-owned pre-start hook; workflow code cannot choose its input.
set -euo pipefail
tmp=$(mktemp); trap 'rm -f -- "$tmp"' EXIT
{ if /usr/sbin/nft list table inet mag_ci_egress >/dev/null 2>&1; then echo 'delete table inet mag_ci_egress'; fi
 cat /etc/mag-ci/egress.nft
} > "$tmp"
/usr/sbin/nft -f "$tmp"
