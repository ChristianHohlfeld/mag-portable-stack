#!/usr/bin/env bash
# Keep the release just installed and one fallback (the previous release).
# A symlink elsewhere does not pin an older release. A release a running
# process still has open is kept and reported. Delete every other release
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
# MAG_RELEASE_SCAN is unused. A symlink under /etc or /usr/local must not
# keep a third release on disk.
: "${scan:-}"
release_in_use() {
  local path=$1 exe cwd p
  for p in /proc/[0-9]*; do
    exe=$(readlink "$p/exe" 2>/dev/null || true)
    cwd=$(readlink "$p/cwd" 2>/dev/null || true)
    [[ "$exe" == "$path"/* || "$cwd" == "$path"/* ]] && return 0
  done
  return 1
}
prune_one() {
  local base=$1 id
  [[ -d "$base" ]] || return 0
  for dir in "$base"/*; do
    [[ -d "$dir" ]] || continue
    id=$(basename "$dir")
    [[ "$id" =~ ^[A-Za-z0-9._-]+$ ]] || continue
    [[ -n "${keep[$id]:-}" ]] && continue
    if release_in_use "$dir"; then
      echo "Keeping $dir: a running process is using it"
      continue
    fi
    echo "Pruning $base/$id"
    rm -rf -- "$dir"
  done
}
prune_one "$root"
prune_one "$backups"
