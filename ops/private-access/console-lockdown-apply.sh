#!/usr/bin/env bash
set -euo pipefail
if nft list table inet mag_console_only >/dev/null 2>&1; then
  nft delete table inet mag_console_only
fi
nft -f /etc/nftables.d/mag-console-only.nft
