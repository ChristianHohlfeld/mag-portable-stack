#!/usr/bin/env bash
# A successful archive means the local encrypted restic snapshot is complete.
set -euo pipefail
HERE=$(cd -- "$(dirname -- "$0")" && pwd)
source "$HERE/common.sh"
check_root
kind="${1:-daily}"
case "$kind" in daily|deploy|clear) ;; *) echo 'Usage: backup.sh daily|deploy|clear|status' >&2; exit 2 ;; esac
backup_lock
started=$(date +%s)
work="$MAG_BACKUP_WORK/payload"
payload="$work/root"
finish() {
  local result=$?
  trap - EXIT
  rm -rf -- "$work"
  if (( result != 0 )); then
    status_update --arg error "Backup ${kind} failed (exit ${result})" \
      --argjson at "$(date +%s)" '.last_error=$error | .last_failure=$at | .healthy=false' || :
    logger -p daemon.err -t mag-backup "FAILED ${kind}: exit ${result}" 2>/dev/null || :
  fi
  exit "$result"
}
trap finish EXIT
rm -rf -- "$work"
install -d -m 0700 "$payload"
src="${MAG_BACKUP_SOURCE%/}"
[[ -n "$src" ]] || src=""
copy_path() {
  local p="$1" from into
  from="$src$p"
  into="$payload$p"
  [[ -e "$from" || -L "$from" ]] || return 0
  mkdir -p "$(dirname "$into")"
  cp -a --reflink=auto -- "$from" "$into"
}
# Copy small volatile state early; validate against live bytes and retry.
snapshot_room() {
  local from="$src/var/lib/magbot/room" into="$payload/var/lib/magbot/room"
  [[ -d "$from" ]] || { echo 'Room state missing' >&2; return 1; }
  mkdir -p "$(dirname "$into")"
  for attempt in 1 2 3; do
    rm -rf -- "$into"
    cp -a --reflink=auto -- "$from" "$into"
    # Applications atomically replace JSON files. Fail rather than silently
    # archive a changed file; only directory tree relevant to room state.
    if diff -qr --exclude='*.tmp' "$from" "$into" >/dev/null 2>&1; then return 0; fi
    sleep 1
  done
  echo 'Room changed during short snapshot; refusing inconsistent backup' >&2
  return 1
}
snapshot_room
if [[ "${MAG_BACKUP_TEST_MODE:-0}" != 1 ]]; then
  for essential in /var/lib/gitea/data/gitea.db /etc/gitea/app.ini \
     /etc/magbot /var/lib/mag-pages /etc/mag-pages /etc/mag-acme /etc/nginx; do
    [[ -e "$src$essential" ]] || { echo "Essential MAG data missing: $essential" >&2; exit 1; }
  done
fi
# Gitea: SQLite online backup; copy repositories and private application
# configuration. Neither Gitea nor the LLM is stopped.
if [[ -f "$src/var/lib/gitea/data/gitea.db" ]]; then
  command -v sqlite3 >/dev/null
  copy_path /var/lib/gitea
  for rel in indexers sessions tmp queues repo-archive; do
    rm -rf -- "$payload/var/lib/gitea/data/$rel"
  done
  rm -rf -- "$payload/var/lib/gitea/log"
  rm -f -- "$payload/var/lib/gitea/data/gitea.db" \
    "$payload/var/lib/gitea/data/gitea.db-wal" "$payload/var/lib/gitea/data/gitea.db-shm"
  sqlite3 "$src/var/lib/gitea/data/gitea.db" \
    ".timeout 10000" ".backup '$payload/var/lib/gitea/data/gitea.db'"
  sqlite3 "$payload/var/lib/gitea/data/gitea.db" 'PRAGMA integrity_check;' | grep -qx ok
fi
for p in /etc/gitea /var/lib/mag-pages /etc/mag-pages /etc/mag-acme \
  /etc/mag-nginx /etc/nginx /etc/magbot /etc/default/mag-room \
  /etc/mag-stack /etc/credstore/mag-guest-oauth \
  /etc/mag-audit /var/lib/mag-audit /etc/mag-llm /var/lib/mag-deploy \
  /var/lib/mag-private-cutover /home/chris/llama-templates \
  /usr/local/lib/mag-deepseek /etc/systemd/system \
  /etc/crontab /etc/cron.d /etc/cron.hourly /etc/cron.daily \
  /etc/cron.weekly /etc/cron.monthly /var/spool/cron/crontabs \
  /var/lib/tailscale; do
  copy_path "$p"
done
# Cron and Tailscale private node identity are backed up only inside encrypted
# restic. Keep backup credentials, model weights and slot cache excluded.
mkdir -p "$payload/var/lib/mag-llm" "$payload/opt/mag-stack"
for name in verified.stamp status.json; do copy_path "/var/lib/mag-llm/$name"; done
if [[ -L "$src/opt/mag-stack/current" ]]; then
  readlink "$src/opt/mag-stack/current" > "$payload/opt/mag-stack/current-release.txt"
fi
if [[ -f "$src/var/lib/mag-llm/verified.stamp" ]]; then
  cp -a "$src/var/lib/mag-llm/verified.stamp" "$payload/opt/mag-stack/model-verification.stamp"
fi
if [[ -d "$src/etc/systemd/system" ]]; then
  # Restrict the inventory to MAG/Gitea plus their drop-ins.
  find "$payload/etc/systemd/system" -mindepth 1 -maxdepth 1 \
    ! -name 'mag-*' ! -name 'gitea*' -exec rm -rf -- {} +
fi
# Cron data including the S23 user jobs was copied above; inventory
# is stored separately and does not expose job secrets.
if [[ "${MAG_BACKUP_TEST_MODE:-0}" != 1 ]]; then
  mkdir -p "$payload/opt/mag-stack"
  (systemctl list-unit-files 'mag-*' 'gitea*' --no-legend 2>/dev/null || :) > "$payload/opt/mag-stack/units.txt"
fi
printf '%s\n' "MAG $(date -u +%FT%TZ) kind=$kind" > "$payload/opt/mag-stack/backup-info.txt"
# Final hard excludes defend against accidentally copied model files.
find "$payload" -type f \( -name '*.gguf' -o -name 'ggml-*.bin' -o -name '*.qcow2' \) -delete
# Stable SHA256 manifest inside the encrypted snapshot, used by monthly
# restore-test and safe dry-run verification. No secret contents are logged.
(
 cd "$payload"
 find . -type f ! -path './opt/mag-stack/checksums.sha256' -print0 |
   sort -z | xargs -0 -r sha256sum > opt/mag-stack/checksums.sha256
)
if ! restic snapshots --json >/dev/null 2>&1; then
  # Initialize only a truly absent repository; existing damaged repos fail closed.
  if [[ ! -e "$MAG_BACKUP_REPO/config" && ! -d "$MAG_BACKUP_REPO/snapshots" ]]; then
    restic init
  else
    echo 'Local restic repo inaccessible or damaged' >&2; exit 1
  fi
fi
case "$kind" in daily) tag=daily ;; deploy) tag=hook-deploy ;; clear) tag=hook-clear ;; esac
# --json emits snapshot metadata, never secret content.
snapshot_json=$(restic backup --json --host mag --tag "$tag" "$work" |
  jq -sc '[.[] | select(.message_type=="summary")][-1]')
snapshot_id=$(jq -r '.snapshot_id // empty' <<<"$snapshot_json")
[[ "$snapshot_id" =~ ^[0-9a-f]{8,64}$ ]] || { echo 'Restic snapshot ID missing' >&2; exit 1; }
bytes=$(jq -r '.total_bytes_processed // 0' <<<"$snapshot_json")
repo_bytes=$(du -sb "$MAG_BACKUP_REPO" | cut -f1)
# One local encrypted repository; snapshots for events receive separate tags.
# Offsite is an immutable COMPLETE COPY of the encrypted repo, never a directly
# writable restic repo (restic prune/unlock conflict with Object Lock).
offsite=false
if s3_enabled; then
  load_aws
  # Refuse an unprotected bucket or a retention shorter than 30 days.
  aws s3api get-object-lock-configuration --bucket "$MAG_BACKUP_BUCKET" --output json |
    jq -e '.ObjectLockConfiguration.ObjectLockEnabled == "Enabled" and
      .ObjectLockConfiguration.Rule.DefaultRetention.Mode == "COMPLIANCE" and
      (.ObjectLockConfiguration.Rule.DefaultRetention.Days // 0) >= 30' >/dev/null || {
      echo 'S3 Object Lock COMPLIANCE >=30 days required' >&2; exit 1;
    }
  # Limit AWS high-level multipart/sync transfer bandwidth on this run only.
  export AWS_CONFIG_FILE="$MAG_BACKUP_WORK/aws-cli-config"
  printf '[default]\ns3 =\n    max_bandwidth = %s\n' "${MAG_BACKUP_S3_BANDWIDTH:-4MB/s}" > "$AWS_CONFIG_FILE"
  chmod 0600 "$AWS_CONFIG_FILE"
  archive="$(date -u +%Y%m%dT%H%M%SZ)-${snapshot_id:0:12}"
  aws s3 sync "$MAG_BACKUP_REPO/" "s3://$MAG_BACKUP_BUCKET/archives/$archive/" \
    --exclude 'locks/*' --no-progress --only-show-errors --region eu-central-1
  # Publish locator LAST; old versions are also protected by bucket Object Lock.
  printf '{"archive":"%s","snapshot":"%s"}\n' "$archive" "$snapshot_id" |
    aws s3 cp - "s3://$MAG_BACKUP_BUCKET/latest.json" --region eu-central-1 --only-show-errors
  offsite=true
fi
finished=$(date +%s)
status_update --arg id "$snapshot_id" --arg kind "$kind" \
  --argjson at "$finished" --argjson bytes "$bytes" --argjson repo_bytes "$repo_bytes" \
  --argjson seconds "$((finished-started))" --argjson offsite "$offsite" \
  '.last_success=$at | .last_snapshot=$id | .last_kind=$kind | .last_bytes=$bytes | .repo_bytes=$repo_bytes | .last_duration_sec=$seconds | .offsite_ok=$offsite | .last_error="" | .healthy=true'
logger -t mag-backup "OK kind=$kind id=$snapshot_id bytes=$bytes seconds=$((finished-started)) offsite=$offsite" 2>/dev/null || :
printf 'Backup %s: %s bytes=%s duration=%ss offsite=%s\n' "$kind" "$snapshot_id" "$bytes" "$((finished-started))" "$offsite"
