#!/usr/bin/env bash
# An empty legacy repositories directory must not hide gitea-repositories,
# and a host with no git repositories must still produce a verified backup.
set -euo pipefail
here=$(cd -- "$(dirname -- "$0")/.." && pwd)
command -v restic >/dev/null
command -v sqlite3 >/dev/null
tmp=$(mktemp -d)
trap 'rm -rf -- "$tmp"' EXIT
export MAG_BACKUP_TEST_MODE=1 MAG_BACKUP_CONFIG="$tmp/config" MAG_BACKUP_MIN_FREE_MIB=64
unset RESTIC_PASSWORD RESTIC_PASSWORD_FILE RESTIC_PASSWORD_COMMAND RESTIC_REPOSITORY || true
seed() {
  local src=$1
  mkdir -p "$src/var/lib/magbot/room" "$src/etc/gitea" "$src/etc/magbot" \
    "$src/etc/mag-nginx/tls" "$src/etc/mag-pages" "$src/var/lib/mag-pages"
  printf '{"messages":["keep"]}\n' > "$src/var/lib/magbot/room/room.json"
  printf 'cfg\n' > "$src/etc/gitea/app.ini"
  printf 'cert\n' > "$src/etc/mag-nginx/tls/cert.pem"
  printf 'pages\n' > "$src/etc/mag-pages/pages.env"
  mkdir -p "$src/var/lib/gitea/data"
  sqlite3 "$src/var/lib/gitea/data/gitea.db" 'create table keep(v text); insert into keep values ("persisted");'
}
one() {
  local label=$1
  export MAG_BACKUP_SOURCE="$tmp/host-$label" MAG_BACKUP_ROOT="$tmp/full-$label"
  export MAG_BACKUP_WORK="$tmp/work-$label" MAG_BACKUP_STATUS="$tmp/st-$label/status.json"
  export MAG_BACKUP_LOCK="$tmp/run-$label/backup.lock"
  "$here/backup.sh" daily
  "$here/restore.sh" --part gitea --target "$tmp/out-$label"
}
mkdir -p "$tmp/host-real/var/lib/gitea/data/repositories" \
  "$tmp/host-real/var/lib/gitea/data/gitea-repositories/owner/real.git"
seed "$tmp/host-real"
printf 'real-head\n' > "$tmp/host-real/var/lib/gitea/data/gitea-repositories/owner/real.git/HEAD"
one real
grep -Rqs 'real-head' "$tmp/out-real"
mkdir -p "$tmp/host-empty/var/lib/gitea/data/repositories" \
  "$tmp/host-empty/var/lib/gitea/data/gitea-repositories"
seed "$tmp/host-empty"
one empty
echo 'gitea root selection OK'
