#!/usr/bin/env bash
# Fully isolated local test; never touches host services or real backup repo.
set -euo pipefail
here=$(cd -- "$(dirname -- "$0")/.." && pwd)
for cmd in restic sqlite3 jq flock sha256sum; do command -v "$cmd" >/dev/null || { echo "Missing $cmd" >&2; exit 1; }; done
tmp=$(mktemp -d)
trap 'rm -rf -- "$tmp"' EXIT
export MAG_BACKUP_TEST_MODE=1 MAG_BACKUP_CONFIG="$tmp/config"
export MAG_BACKUP_ROOT="$tmp/full" MAG_BACKUP_WORK="$tmp/work"
export MAG_BACKUP_SOURCE="$tmp/host" MAG_BACKUP_STATUS="$tmp/status/status.json"
export MAG_BACKUP_LOCK="$tmp/run/backup.lock"
export MAG_BACKUP_MIN_FREE_MIB=64
# The multi-archive checks below need older archives; the default (3) is checked at the end.
export MAG_BACKUP_KEEP=3
unset RESTIC_REPOSITORY || true
mkdir -p "$MAG_BACKUP_CONFIG" "$MAG_BACKUP_SOURCE/var/lib/magbot/room/files" \
 "$MAG_BACKUP_SOURCE/var/lib/gitea/data/repositories/magbot-agent1/memory.git" \
 "$MAG_BACKUP_SOURCE/etc/gitea" "$MAG_BACKUP_SOURCE/var/lib/mag-pages" \
 "$MAG_BACKUP_SOURCE/etc/magbot" "$MAG_BACKUP_SOURCE/var/lib/mag-llm" \
 "$MAG_BACKUP_SOURCE/var/lib/tailscale" "$MAG_BACKUP_SOURCE/var/spool/cron/crontabs" "$MAG_BACKUP_SOURCE/etc/cron.d" \
 "$MAG_BACKUP_SOURCE/etc/mag-stack" "$MAG_BACKUP_SOURCE/etc/credstore"
# Passwordless: zero credential files and no password environment variables.
unset RESTIC_PASSWORD RESTIC_PASSWORD_FILE RESTIC_PASSWORD_COMMAND
restic() { command restic --insecure-no-password "$@"; }
test ! -e "$MAG_BACKUP_CONFIG/restic-password"
printf 'forbidden model\\n' > "$MAG_BACKUP_SOURCE/var/lib/magbot/room/files/not-backed-up.gguf"
printf '{"messages":["do not lose me"]}\n' > "$MAG_BACKUP_SOURCE/var/lib/magbot/room/room.json"
printf 'payload\n' > "$MAG_BACKUP_SOURCE/var/lib/magbot/room/files/upload.txt"
printf 'repo\n' > "$MAG_BACKUP_SOURCE/var/lib/gitea/data/repositories/magbot-agent1/memory.git/HEAD"
printf 'private-config\n' > "$MAG_BACKUP_SOURCE/etc/gitea/app.ini"
printf 'webroot\n' > "$MAG_BACKUP_SOURCE/var/lib/mag-pages/sites.json"
mkdir -p "$MAG_BACKUP_SOURCE/var/lib/mag-pages/portal" \
  "$MAG_BACKUP_SOURCE/etc/mag-pages" \
  "$MAG_BACKUP_SOURCE/etc/mag-nginx/tls" \
  "$MAG_BACKUP_SOURCE/etc/nginx/mag-tls" \
  "$MAG_BACKUP_SOURCE/opt/mag-stack/releases/old/build"
printf 'portal\n' > "$MAG_BACKUP_SOURCE/var/lib/mag-pages/portal/index.html"
printf 'pages-env\n' > "$MAG_BACKUP_SOURCE/etc/mag-pages/pages.env"
printf 'room-cert\n' > "$MAG_BACKUP_SOURCE/etc/mag-nginx/tls/cert.pem"
printf 'room-key\n' > "$MAG_BACKUP_SOURCE/etc/mag-nginx/tls/key.pem"
printf 'compile-object\n' > "$MAG_BACKUP_SOURCE/opt/mag-stack/releases/old/build/a.o"
printf 'vm-disk\n' > "$MAG_BACKUP_SOURCE/var/lib/magbot/room/files/disk.qcow2"
printf 'secret\n' > "$MAG_BACKUP_SOURCE/etc/magbot/auth.json"
printf 'owner-probe-fixture\n' > "$MAG_BACKUP_SOURCE/etc/mag-stack/deploy-owner-probe.conf"
printf 'dummy-guest-oauth-fixture\n' > "$MAG_BACKUP_SOURCE/etc/credstore/mag-guest-oauth"
printf 'encrypted-tailnet-state\n' > "$MAG_BACKUP_SOURCE/var/lib/tailscale/tailscaled.state"
printf '* * * * * /usr/bin/true\n' > "$MAG_BACKUP_SOURCE/var/spool/cron/crontabs/chris"
printf '15 4 * * * root /usr/bin/true\n' > "$MAG_BACKUP_SOURCE/etc/cron.d/mag-health"
sqlite3 "$MAG_BACKUP_SOURCE/var/lib/gitea/data/gitea.db" 'create table keep(v text); insert into keep values ("persisted");'
# S3 offsite was removed. Any `aws` invocation fails the run.
mkdir -p "$tmp/fakebin"
cat > "$tmp/fakebin/aws" <<'AWS'
#!/usr/bin/env bash
echo 'FAIL: AWS invoked; backups are local-only' >&2
exit 99
AWS
chmod +x "$tmp/fakebin/aws"
export PATH="$tmp/fakebin:$PATH"
# First, a local backup with no aws.env at all.
"$here/backup.sh" daily 2>"$tmp/daily.err"
if grep -Fq 'aws.env' "$tmp/daily.err"; then echo 'aws.env note without aws.env' >&2; exit 1; fi
"$here/maintenance.sh" status | jq -e '.offsite_ok == false and .healthy == true' >/dev/null
# A leftover aws.env is ignored (one-line note, never sourced or printed).
cat > "$MAG_BACKUP_CONFIG/aws.env" <<'AWS'
AWS_ACCESS_KEY_ID=DO_NOT_USE_TEST_KEY
AWS_SECRET_ACCESS_KEY=DO_NOT_USE_TEST_SECRET
AWS
"$here/backup.sh" deploy >"$tmp/deploy.out" 2>&1
grep -Fq 'aws.env is unused' "$tmp/deploy.out"
if grep -Fq 'DO_NOT_USE_TEST' "$tmp/deploy.out"; then echo 'aws.env contents printed' >&2; exit 1; fi
"$here/backup.sh" clear
mapfile -t archives < <(find "$MAG_BACKUP_ROOT" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' | LC_ALL=C sort)
[[ ${#archives[@]} -eq 3 ]]
for id in "${archives[@]}"; do
  [[ -f "$MAG_BACKUP_ROOT/$id/VERIFIED" ]]
  [[ "$(restic -r "$MAG_BACKUP_ROOT/$id/repo" snapshots --json | jq 'length')" -eq 1 ]]
done
restic -r "$MAG_BACKUP_ROOT/${archives[0]}/repo" snapshots --json | jq -e '.[0].tags[0]=="daily"' >/dev/null
restic -r "$MAG_BACKUP_ROOT/${archives[1]}/repo" snapshots --json | jq -e '.[0].tags[0]=="hook-deploy"' >/dev/null
restic -r "$MAG_BACKUP_ROOT/${archives[2]}/repo" snapshots --json | jq -e '.[0].tags[0]=="hook-clear"' >/dev/null
"$here/maintenance.sh" check
"$here/maintenance.sh" restore-test
# A restore-test must verify the materialized --target tree, not the restic wrapper.
grep -Fq 'payload="$tmp/recovered"' "$here/maintenance.sh"
"$here/restore.sh" --part room --target "$tmp/partial"
"$here/restore.sh" --part gitea --target "$tmp/partial-git"
"$here/restore.sh" --part pages --target "$tmp/partial-pages"
"$here/restore.sh" --part cron --target "$tmp/partial-cron"
"$here/restore.sh" --part tailscale --target "$tmp/partial-tailscale"
grep -Rqs 'encrypted-tailnet-state' "$tmp/partial-tailscale"
grep -Rqs '/usr/bin/true' "$tmp/partial-cron"
"$here/restore.sh" --part config --target "$tmp/partial-config"
grep -Fqx 'owner-probe-fixture' "$tmp/partial-config/etc/mag-stack/deploy-owner-probe.conf"
grep -Fqx 'dummy-guest-oauth-fixture' "$tmp/partial-config/etc/credstore/mag-guest-oauth"
grep -Rqs 'do not lose me' "$tmp/partial/"
grep -Rqs 'repo' "$tmp/partial-git/"
grep -Rqs 'room-cert' "$tmp/partial-config/"
grep -Rqs 'portal' "$tmp/partial-pages/"
! grep -Rqs 'compile-object' "$tmp/partial" "$tmp/partial-git" "$tmp/partial-pages" "$tmp/partial-config"
! grep -Rqs 'vm-disk' "$tmp/partial"
grep -Rqs 'persisted' "$tmp/partial-git/" || {
 db=$(find "$tmp/partial-git" -name gitea.db -print -quit)
 [[ "$(sqlite3 "$db" 'select v from keep')" == persisted ]]
}
grep -Rqs webroot "$tmp/partial-pages"
if find "$tmp/partial" -name '*.gguf' | grep -q .; then echo 'GGUF leaked' >&2; exit 1; fi
# This known sentinel was present on disk before backup, but MUST NOT be restored.
! grep -Rqs 'forbidden model' "$tmp/partial"
"$here/maintenance.sh" retain
"$here/maintenance.sh" status | jq -e '.stale == false and .last_restore_test_ok == true' >/dev/null
! grep -R -E '(systemctl|service) (stop|restart) mag-llm' "$here"/*.sh
test ! -e "$MAG_BACKUP_CONFIG/restic-password"
verified_n() { find "$MAG_BACKUP_ROOT" -mindepth 2 -maxdepth 2 -name VERIFIED | wc -l; }
# One archive restores alone. Hide the other repositories first.
alone=${archives[0]}
mkdir -p "$tmp/held"
for id in "${archives[@]:1}"; do mv "$MAG_BACKUP_ROOT/$id" "$tmp/held/"; done
"$here/restore.sh" --snapshot "$alone" --part room --target "$tmp/alone"
grep -Rqs 'do not lose me' "$tmp/alone"
for id in "${archives[@]:1}"; do mv "$tmp/held/$id" "$MAG_BACKUP_ROOT/"; done
# Keep the last 3 verified archives plus the pre-deploy backup of one release.
export MAG_BACKUP_RELEASE_ID=rel-keep MAG_BACKUP_PROTECT_RELEASE=rel-keep
"$here/backup.sh" deploy
unset MAG_BACKUP_RELEASE_ID
for n in 1 2 3 4; do
  printf 'later-room %s\n' "$n" > "$MAG_BACKUP_SOURCE/var/lib/magbot/room/room.json"
  "$here/backup.sh" daily
done
[[ "$(verified_n)" -eq 4 ]]
found=0
while IFS= read -r meta; do
  grep -Fq 'kind=deploy' "$meta" && grep -Fq 'release=rel-keep' "$meta" && found=1
done < <(find "$MAG_BACKUP_ROOT" -name META)
[[ "$found" -eq 1 ]]
before=$(verified_n)
if MAG_BACKUP_MIN_FREE_MIB=99999999 "$here/backup.sh" daily >"$tmp/space.out" 2>&1; then
  echo 'Low disk space was accepted' >&2; exit 1
fi
grep -Fq 'MAG backup refused' "$tmp/space.out"
grep -Fq 'no old backup was deleted' "$tmp/space.out"
[[ "$(verified_n)" -eq "$before" ]]
if MAG_BACKUP_MIN_FREE_MIB=64 MAG_BACKUP_TEST_ABORT=1 "$here/backup.sh" daily >"$tmp/abort.out" 2>&1; then
  echo 'Aborted backup was treated as success' >&2; exit 1
fi
grep -Fq 'verified backups were not deleted' "$tmp/abort.out"
[[ "$(verified_n)" -eq "$before" ]]
! find "$MAG_BACKUP_ROOT" -mindepth 1 -maxdepth 1 -type d ! -exec test -f '{}/VERIFIED' \; -print | grep -q .
! grep -Fq 'restic forget' "$here/backup.sh"
open="$tmp/world-repo"
mkdir -m 0777 "$open"
if MAG_BACKUP_TEST_MODE=0 MAG_BACKUP_ROOT="$open" bash -c "source '$here/common.sh'" >"$tmp/rootcheck.out" 2>&1; then
  echo 'Non-root backup root was accepted' >&2; exit 1
fi
grep -Eq 'root-owned|inaccessible to group/other' "$tmp/rootcheck.out"
# A stale MAG_BACKUP_OFFSITE_ENABLED=1 fails closed before any work.
before=$(verified_n)
if MAG_BACKUP_OFFSITE_ENABLED=1 "$here/backup.sh" daily >"$tmp/offsite-error.out" 2>&1; then
 echo 'Stale offsite opt-in was silently accepted' >&2; exit 1
fi
grep -Fq 'S3 offsite was removed; set MAG_BACKUP_OFFSITE_ENABLED=0 or delete the line' "$tmp/offsite-error.out"
[[ "$(verified_n)" -eq "$before" ]]
if "$here/restore.sh" --repo offsite --part room --target "$tmp/offsite-restore" >"$tmp/offsite-restore.out" 2>&1; then
 echo 'Offsite restore mode still accepted' >&2; exit 1
fi
grep -Fq 'S3 offsite was removed' "$tmp/offsite-restore.out"
if grep -R -E '\baws (s3|s3api)\b|s3://|MAG_BACKUP_BUCKET|load_aws|s3_enabled' "$here"/*.sh; then
 echo 'S3 offsite code remains' >&2; exit 1
fi
rm -f "$MAG_BACKUP_CONFIG/aws.env"
# Default: the last 3 verified full backups remain (the protected deploy
# archive is no longer held once MAG_BACKUP_PROTECT_RELEASE is unset).
unset MAG_BACKUP_PROTECT_RELEASE MAG_BACKUP_KEEP
"$here/backup.sh" daily
[[ "$(verified_n)" -eq 3 ]]
pr="$tmp/prune"
mkdir -p "$pr/releases"/{current-id,previous-id,old-id,linked-id} \
  "$pr/backups"/{current-id,previous-id,old-id,orphan} "$pr/scan"
ln -s "$pr/releases/current-id" "$pr/current"
ln -s "$pr/releases/linked-id" "$pr/scan/live"
printf '%s\n' "$pr/releases/previous-id" > "$pr/previous"
MAG_RELEASES_DIR="$pr/releases" MAG_RELEASE_BACKUPS="$pr/backups" \
  MAG_RELEASE_CURRENT="$pr/current" MAG_RELEASE_PREVIOUS_FILE="$pr/previous" \
  MAG_RELEASE_ID=current-id MAG_RELEASE_SCAN="$pr/scan" \
  bash "$here/../prune-releases.sh"
test -d "$pr/releases/current-id"
test -d "$pr/releases/previous-id"
test ! -d "$pr/releases/linked-id"
test ! -d "$pr/releases/old-id"
test ! -d "$pr/backups/old-id"
test ! -d "$pr/backups/orphan"
test -d "$pr/backups/current-id"
echo 'MAG encrypted backup / selective restore / checksum / retention OK'
