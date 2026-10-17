#!/usr/bin/env bash
# Keep the release just installed, the current symlink, the previous release,
# and any release still targeted by a symlink. Delete every other release
# directory and the matching rollback directory under /var/backups/mag-stack.
set -euo pipefail
root=${MAG_RELEASES_DIR:-/opt/mag-stack/releases}
backups=${MAG_RELEASE_BACKUPS:-/var/backups/mag-stack}
current_link=${MAG_RELEASE_CURRENT:-/opt/mag-stack/current}
prev_file=${MAG_RELEASE_PREVIOUS_FILE:-}
this=${MAG_RELEASE_ID:-}
scan=${MAG_RELEASE_SCAN:-/etc /usr/local /opt/mag-stack}
[[ -d "$root" ]] || exit 0
declare -A keep=()
[[ -n "$this" ]] && keep["$this"]=1
if [[ -L "$current_link" ]]; then
  id=$(basename "$(readlink -f "$current_link")")
  [[ "$id" =~ ^[A-Za-z0-9._-]+$ ]] && keep["$id"]=1
fi
if [[ -n "$prev_file" && -f "$prev_file" ]]; then
  id=$(basename "$(cat "$prev_file")")
  [[ "$id" =~ ^[A-Za-z0-9._-]+$ ]] && keep["$id"]=1
fi
# shellcheck disable=SC2086
while IFS= read -r link; do
  target=$(readlink -f "$link" 2>/dev/null || true)
  [[ "$target" == "$root"/* ]] || continue
  id=${target#"$root"/}
  id=${id%%/*}
  [[ "$id" =~ ^[A-Za-z0-9._-]+$ ]] && keep["$id"]=1
done < <(find $scan -xdev -type l 2>/dev/null || true)
prune_one() {
  local base=$1 id
  [[ -d "$base" ]] || return 0
  for dir in "$base"/*; do
    [[ -d "$dir" ]] || continue
    id=$(basename "$dir")
    [[ "$id" =~ ^[A-Za-z0-9._-]+$ ]] || continue
    [[ -n "${keep[$id]:-}" ]] && continue
    echo "Pruning unreferenced $base/$id"
    rm -rf -- "$dir"
  done
}
prune_one "$root"
prune_one "$backups"
