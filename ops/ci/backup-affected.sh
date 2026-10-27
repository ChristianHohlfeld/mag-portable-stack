#!/usr/bin/env bash
# Exit 0 when backup/ files changed and the restore e2e must run.
# Exit 1 when it can be skipped. Unknown diffs fail open (run).
#   MAG_CI_FORCE_BACKUP=1  always run
#   MAG_CI_BASE            git base (default origin/main)
set -euo pipefail
ROOT=$(cd -- "$(dirname -- "$(readlink -f "$0")")/../.." && pwd)
cd "$ROOT"
if [[ ${MAG_CI_FORCE_BACKUP:-0} == 1 ]]; then
  exit 0
fi
backup_path() {
  case "$1" in
    backup/*|ops/backup/*|.github/workflows/backup.yml) return 0 ;;
    *) return 1 ;;
  esac
}
if [[ -n ${MAG_AFFECTED_FILES:-} ]]; then
  while IFS= read -r path; do
    [[ -z $path ]] && continue
    backup_path "$path" && exit 0
  done <<<"$MAG_AFFECTED_FILES"
  exit 1
fi
base=${MAG_CI_BASE:-}
if [[ -z $base ]]; then
  if git rev-parse --verify -q origin/main >/dev/null; then
    base=origin/main
  elif git rev-parse --verify -q main >/dev/null; then
    base=main
  else
    exit 0
  fi
fi
head=${MAG_CI_SHA:-HEAD}
mapfile -t files < <(
  git diff --name-only "$base"... "$head" 2>/dev/null || git diff --name-only "$base" "$head" 2>/dev/null || true
  git diff --name-only
  git ls-files --others --exclude-standard
)
if ((${#files[@]} == 0)) && ! git rev-parse --verify -q "$base" >/dev/null; then
  exit 0
fi
for path in "${files[@]}"; do
  [[ -z $path ]] && continue
  backup_path "$path" && exit 0
done
exit 1
