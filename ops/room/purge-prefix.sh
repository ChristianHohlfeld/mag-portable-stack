#!/usr/bin/env bash
# Owner-authorized room clear only. Never touch GGUFs or clean system prefixes.
set -euo pipefail
root=/var/lib/mag-llm/slots
[[ -d "$root" ]] || exit 0
# Only stale room transcript saves. Files named mag-clean-* remain usable.
# -P: never traverse symlinked directories. -xdev: stay on slot filesystem.
find -P "$root" -xdev -mindepth 1 -maxdepth 5 -type f \
  -name 'mag-prefix-*.bin' -delete
