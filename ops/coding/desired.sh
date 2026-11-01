#!/usr/bin/env bash
# Emit the effective selected layout, honoring the existing operator pin.
set -euo pipefail
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
if [[ -n ${MAG_ROOM_LAYOUT:-} && ${MAG_ROOM_LAYOUT} != repo ]]; then
  printf '%s\n' "$MAG_ROOM_LAYOUT"
elif [[ -z ${MAG_ROOM_LAYOUT:-} && -s /etc/mag-stack/room-layout.pinned ]]; then
  cat /etc/mag-stack/room-layout.pinned
else
  cat "$root/ops/room/layout"
fi
