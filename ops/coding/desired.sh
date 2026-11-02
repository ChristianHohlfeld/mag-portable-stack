#!/usr/bin/env bash
# Emit the effective selected layout, honoring the existing operator pin.
set -euo pipefail
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
if [[ -n ${MAG_ROOM_LAYOUT:-} && ${MAG_ROOM_LAYOUT} != repo ]]; then
  printf '%s\n' "$MAG_ROOM_LAYOUT"
elif [[ -z ${MAG_ROOM_LAYOUT:-} && -s /etc/mag-stack/room-layout.pinned ]]; then
  pinned=$(tr -d '[:space:]' < /etc/mag-stack/room-layout.pinned)
  target=$(tr -d '[:space:]' < "$root/ops/room/layout")
  # This owner-approved change replaces the stale GLM pin after staging.
  # Other operator pins and any explicit MAG_ROOM_LAYOUT still take precedence.
  if [[ $pinned == glm && $target == coding ]]; then
    printf '%s\n' "$target"
  else
    printf '%s\n' "$pinned"
  fi
else
  cat "$root/ops/room/layout"
fi
