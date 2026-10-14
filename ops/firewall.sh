#!/usr/bin/env bash
set -euo pipefail
RULES="$(dirname -- "$0")/mag-agents.nft"
if nft list table inet mag_agents >/dev/null 2>&1; then
 { printf 'delete table inet mag_agents\n'; cat "$RULES"; } | nft -f -
else
 nft -f "$RULES"
fi
