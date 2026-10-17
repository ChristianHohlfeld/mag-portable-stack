#!/usr/bin/env bash
# Fully isolated local test; never touches host services or real backup repo.
set -euo pipefail
here=$(cd -- "$(dirname -- "$0")/.." && pwd)
for cmd in restic sqlite3 jq flock sha256sum; do command -v "$cmd" >/dev/null || { echo "Missing $cmd" >&2; exit 1; }; done
tmp=$(mktemp -d)
trap 'rm -rf -- "$tmp"' EXIT
export MAG_BACKUP_TEST_MODE=1 MAG_BACKUP_CONFIG="$tmp/config"
export MAG_BACKUP_REPO="$tmp/repo" MAG_BACKUP_WORK="$tmp/work"
export MAG_BACKUP_SOURCE="$tmp/host" MAG_BACKUP_STATUS="$tmp/status/status.json"
export MAG_BACKUP_LOCK="$tmp/run/backup.lock"
mkdir -p "$MAG_BACKUP_CONFIG" "$MAG_BACKUP_SOURCE/var/lib/magbot/room/files" \
 "$MAG_BACKUP_SOURCE/var/lib/gitea/data/repositories/magbot-agent1/memory.git" \
 "$MAG_BACKUP_SOURCE/etc/gitea" "$MAG_BACKUP_SOURCE/var/lib/mag-pages" \
 "$MAG_BACKUP_SOURCE/etc/magbot" "$MAG_BACKUP_SOURCE/var/lib/mag-llm" \
 "$MAG_BACKUP_SOURCE/var/lib/tailscale" "$MAG_BACKUP_SOURCE/var/spool/cron/crontabs" "$MAG_BACKUP_SOURCE/etc/cron.d" \
 "$MAG_BACKUP_SOURCE/etc/mag-stack" "$MAG_BACKUP_SOURCE/etc/credstore"
printf '%s\n' 'local-only-recovery-password' > "$MAG_BACKUP_CONFIG/restic-password"
chmod 0600 "$MAG_BACKUP_CONFIG/restic-password"
export RESTIC_PASSWORD_FILE="$MAG_BACKUP_CONFIG/restic-password"
printf 'forbidden model\\n' > "$MAG_BACKUP_SOURCE/var/lib/magbot/room/files/not-backed-up.gguf"
printf '{"messages":["do not lose me"]}\n' > "$MAG_BACKUP_SOURCE/var/lib/magbot/room/room.json"
printf 'payload\n' > "$MAG_BACKUP_SOURCE/var/lib/magbot/room/files/upload.txt"
printf 'repo\n' > "$MAG_BACKUP_SOURCE/var/lib/gitea/data/repositories/magbot-agent1/memory.git/HEAD"
printf 'private-config\n' > "$MAG_BACKUP_SOURCE/etc/gitea/app.ini"
printf 'webroot\n' > "$MAG_BACKUP_SOURCE/var/lib/mag-pages/sites.json"
printf 'secret\n' > "$MAG_BACKUP_SOURCE/etc/magbot/auth.json"
printf 'owner-probe-fixture\n' > "$MAG_BACKUP_SOURCE/etc/mag-stack/deploy-owner-probe.conf"
printf 'dummy-guest-oauth-fixture\n' > "$MAG_BACKUP_SOURCE/etc/credstore/mag-guest-oauth"
printf 'encrypted-tailnet-state\n' > "$MAG_BACKUP_SOURCE/var/lib/tailscale/tailscaled.state"
printf '* * * * * /usr/bin/true\n' > "$MAG_BACKUP_SOURCE/var/spool/cron/crontabs/chris"
printf '15 4 * * * root /usr/bin/true\n' > "$MAG_BACKUP_SOURCE/etc/cron.d/mag-health"
sqlite3 "$MAG_BACKUP_SOURCE/var/lib/gitea/data/gitea.db" 'create table keep(v text); insert into keep values ("persisted");'
# A residual aws.env must not turn on S3. Local-only is the default.
cat > "$MAG_BACKUP_CONFIG/aws.env" <<'AWS'
AWS_ACCESS_KEY_ID=DO_NOT_USE_TEST_KEY
AWS_SECRET_ACCESS_KEY=DO_NOT_USE_TEST_SECRET
AWS_DEFAULT_REGION=eu-central-1
AWS
mkdir -p "$tmp/fakebin"
cat > "$tmp/fakebin/aws" <<'AWS'
#!/usr/bin/env bash
echo 'FAIL: AWS invoked during local-only backup' >&2
exit 99
AWS
chmod +x "$tmp/fakebin/aws"
export PATH="$tmp/fakebin:$PATH"
# First, a local encrypted backup WITHOUT any aws.env at all.
mv "$MAG_BACKUP_CONFIG/aws.env" "$MAG_BACKUP_CONFIG/aws.env.test-pending"
"$here/backup.sh" daily
"$here/maintenance.sh" status | jq -e '.offsite_ok == false and .healthy == true' >/dev/null
# Next, a stale aws.env present: still NO AWS requests.
mv "$MAG_BACKUP_CONFIG/aws.env.test-pending" "$MAG_BACKUP_CONFIG/aws.env"
"$here/backup.sh" deploy
"$here/backup.sh" clear
[[ "$(restic -r "$MAG_BACKUP_REPO" snapshots --json | jq length)" -eq 3 ]]
restic -r "$MAG_BACKUP_REPO" snapshots --json | jq -e 'map(.tags[0]) | sort == ["daily","hook-clear","hook-deploy"]' >/dev/null
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
test -s "$MAG_BACKUP_CONFIG/aws.env"
"$here/maintenance.sh" status | jq -e '.offsite_ok == false and .healthy == true' >/dev/null
# Explicit S3 opt-in without credentials must error rather than silently
# reporting a local-only backup as fully successful.
mv "$MAG_BACKUP_CONFIG/aws.env" "$MAG_BACKUP_CONFIG/aws.env.disabled"
if MAG_BACKUP_OFFSITE_ENABLED=1 "$here/backup.sh" daily >"$tmp/offsite-error.out" 2>&1; then
 echo 'Missing AWS credentials wrongly accepted with offsite explicitly on' >&2; exit 1
fi
grep -Fq 'Offsite enabled but AWS credentials missing/unsafe' "$tmp/offsite-error.out"
echo 'MAG encrypted backup / selective restore / checksum / retention OK'
