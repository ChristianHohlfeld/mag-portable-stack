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
run_dir=
verified_ok=0
finish() {
  local result=$?
  trap - EXIT
  rm -rf -- "$work"
  # A failed run removes only its own incomplete directory. Verified archives stay.
  if (( result != 0 )); then
    if [[ -n "${run_dir:-}" && "${verified_ok:-0}" != 1 && -d "$run_dir" ]]; then
      rm -rf -- "$run_dir"
      echo "Removed incomplete archive $(basename "$run_dir"); verified backups were not deleted" >&2
    fi
    status_update --arg error "Backup ${kind} failed (exit ${result})" \
      --argjson at "$(date +%s)" '.last_error=$error | .last_failure=$at | .healthy=false' || :
    logger -p daemon.err -t mag-backup "FAILED ${kind}: exit ${result}" 2>/dev/null || :
  fi
  exit "$result"
}
trap finish EXIT
require_free_mib() {
  local path=$1 need avail
  need=${MAG_BACKUP_MIN_FREE_MIB:-4096}
  mkdir -p -- "$path"
  avail=$(df -k "$path" | awk 'NR==2 {print int($4/1024)}')
  [[ "$avail" =~ ^[0-9]+$ ]] || {
    echo "MAG backup refused: cannot read free space on $path" >&2
    exit 1
  }
  if (( avail < need )); then
    echo "MAG backup refused: ${avail} MiB free under ${path}, need ${need} MiB. No archive was written and no old backup was deleted." >&2
    logger -p daemon.err -t mag-backup "REFUSED ${kind}: free ${avail} MiB < ${need} MiB on ${path}" 2>/dev/null || :
    exit 1
  fi
}
install -d -m 0700 "$MAG_BACKUP_ROOT"
require_free_mib "$MAG_BACKUP_ROOT"
require_free_mib "$MAG_BACKUP_WORK"
rm -rf -- "$work"
install -d -m 0700 "$payload"
assert_payload() {
  local tree=$1 repo_root="" candidate
  [[ -d "$tree/var/lib/magbot/room" ]] || { echo 'Backup missing room state' >&2; return 1; }
  [[ -s "$tree/var/lib/gitea/data/gitea.db" ]] || { echo 'Backup missing Gitea database' >&2; return 1; }
  [[ -s "$tree/etc/gitea/app.ini" ]] || { echo 'Backup missing Gitea config' >&2; return 1; }
  for candidate in var/lib/gitea/data/repositories var/lib/gitea/data/gitea-repositories; do
    if [[ -d "$tree/$candidate" ]]; then repo_root=$candidate; break; fi
  done
  [[ -n "$repo_root" ]] || { echo 'Backup missing Gitea repository root' >&2; return 1; }
  find "$tree/$repo_root" -mindepth 1 -print -quit | grep -q . || {
    echo 'Backup Gitea repositories empty' >&2; return 1;
  }
  if [[ -e "$src/etc/mag-nginx/tls/cert.pem" || -e "$src/etc/nginx/mag-tls/cert.pem" ]]; then
    [[ -s "$tree/etc/mag-nginx/tls/cert.pem" || -s "$tree/etc/nginx/mag-tls/cert.pem" ]] || {
      echo 'Backup missing TLS certificate that exists on the host' >&2; return 1;
    }
  fi
  if [[ -d "$src/etc/mag-pages" ]]; then
    [[ -d "$tree/etc/mag-pages" ]] || { echo 'Backup missing /etc/mag-pages' >&2; return 1; }
  fi
  if [[ -d "$src/var/lib/mag-pages" ]]; then
    [[ -d "$tree/var/lib/mag-pages" ]] || { echo 'Backup missing Pages webroots' >&2; return 1; }
  fi
  if find "$tree" -type f \( -name '*.gguf' -o -name 'ggml-*.bin' -o -name '*.qcow2' \) | grep -q .; then
    echo 'Backup included a model, GGUF or VM image' >&2; return 1
  fi
  return 0
}
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
  # Pages is optional until first activation. Missing Pages paths must not
  # block the first LOCAL backup required by ops/deploy.sh.
  for essential in /var/lib/gitea/data/gitea.db /etc/gitea/app.ini \
     /etc/magbot /etc/mag-acme /etc/nginx; do
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
assert_payload "$payload"
payload_bytes=$(du -sb "$payload" | awk '{print $1}')
echo "MAG backup payload ${payload_bytes} bytes (room, Gitea db+repos, config; no models)"
# One fresh restic repository per run. Restoring it does not need any other backup.
release=${MAG_BACKUP_RELEASE_ID:-}
if [[ -z "$release" && -r /run/mag-backup-release ]]; then
  release=$(tr -cd 'A-Za-z0-9._-' < /run/mag-backup-release)
fi
stamp=$(date -u +%Y%m%dT%H%M%S%NZ)
run_dir="$MAG_BACKUP_ROOT/${stamp}-${kind}"
install -d -m 0700 "$run_dir" "$run_dir/repo"
printf 'kind=%s\nrelease=%s\ncreated=%s\n' "$kind" "$release" "$stamp" > "$run_dir/META"
chmod 0600 "$run_dir/META"
export RESTIC_REPOSITORY="$run_dir/repo"
restic init >/dev/null
case "$kind" in daily) tag=daily ;; deploy) tag=hook-deploy ;; clear) tag=hook-clear ;; esac
snapshot_json=$(restic backup --json --host mag --tag "$tag" "$work" |
  jq -sc '[.[] | select(.message_type=="summary")][-1]')
snapshot_id=$(jq -r '.snapshot_id // empty' <<<"$snapshot_json")
[[ "$snapshot_id" =~ ^[0-9a-f]{8,64}$ ]] || { echo 'Restic snapshot ID missing' >&2; exit 1; }
bytes=$(jq -r '.total_bytes_processed // 0' <<<"$snapshot_json")
# Test hook: snapshot exists, verification has not passed, so nothing is deleted.
if [[ "${MAG_BACKUP_TEST_ABORT:-0}" == 1 ]]; then
  echo 'MAG backup test abort before verify' >&2
  exit 1
fi
restic check >/dev/null
verify_tmp=$(mktemp -d "$MAG_BACKUP_WORK/verify.XXXXXXXX")
restic restore latest --target "$verify_tmp/out" >/dev/null
restored=$(find "$verify_tmp/out" -type d -name root -path '*/payload/root' -print -quit)
[[ -n "$restored" && -s "$restored/opt/mag-stack/checksums.sha256" ]] || {
  echo 'Verified extract missing MAG payload' >&2; exit 1;
}
(cd "$restored" && sha256sum --quiet -c opt/mag-stack/checksums.sha256)
assert_payload "$restored"
manifest_sha=$(sha256sum "$restored/opt/mag-stack/checksums.sha256" | awk '{print $1}')
rm -rf -- "$verify_tmp"
# Offsite stays opt-in and is not the retention mechanism. A failure here
# still has verified_ok=0, so the new directory is discarded and no older
# archive is removed.
offsite=false
if s3_enabled; then
  load_aws
  aws s3api get-object-lock-configuration --bucket "$MAG_BACKUP_BUCKET" --output json |
    jq -e '.ObjectLockConfiguration.ObjectLockEnabled == "Enabled" and
      .ObjectLockConfiguration.Rule.DefaultRetention.Mode == "COMPLIANCE" and
      (.ObjectLockConfiguration.Rule.DefaultRetention.Days // 0) >= 30' >/dev/null || {
      echo 'S3 Object Lock COMPLIANCE >=30 days required' >&2; exit 1;
    }
  export AWS_CONFIG_FILE="$MAG_BACKUP_WORK/aws-cli-config"
  printf '[default]\ns3 =\n    max_bandwidth = %s\n' "${MAG_BACKUP_S3_BANDWIDTH:-4MB/s}" > "$AWS_CONFIG_FILE"
  chmod 0600 "$AWS_CONFIG_FILE"
  archive="$(date -u +%Y%m%dT%H%M%SZ)-${snapshot_id:0:12}"
  aws s3 sync "$run_dir/repo/" "s3://$MAG_BACKUP_BUCKET/archives/$archive/" \
    --exclude 'locks/*' --no-progress --only-show-errors --region eu-central-1
  printf '{"archive":"%s","snapshot":"%s"}\n' "$archive" "$snapshot_id" |
    aws s3 cp - "s3://$MAG_BACKUP_BUCKET/latest.json" --region eu-central-1 --only-show-errors
  offsite=true
fi
printf 'snapshot=%s\nmanifest=%s\n' "$snapshot_id" "$manifest_sha" >> "$run_dir/VERIFIED"
chmod 0600 "$run_dir/VERIFIED"
verified_ok=1
rotate_full_backups() {
  local -a ids=()
  local id protect="" newest
  mapfile -t ids < <(find "$MAG_BACKUP_ROOT" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' | LC_ALL=C sort)
  local -A keep=()
  local verified=()
  for id in "${ids[@]}"; do
    [[ -f "$MAG_BACKUP_ROOT/$id/VERIFIED" && -d "$MAG_BACKUP_ROOT/$id/repo" ]] || continue
    verified+=("$id")
  done
  (( ${#verified[@]} > 0 )) || return 0
  newest=${verified[-1]}
  keep["$newest"]=1
  # One current full backup is enough on this host (MAG_BACKUP_KEEP=1).
  local start=0 n=${#verified[@]} want=${MAG_BACKUP_KEEP:-1}
  [[ "$want" =~ ^[1-9][0-9]*$ ]] || want=1
  (( n > want )) && start=$((n - want))
  local i
  for ((i=start; i<n; i++)); do keep["${verified[i]}"]=1; done
  protect=${MAG_BACKUP_PROTECT_RELEASE:-}
  if [[ -z "$protect" && -L /opt/mag-stack/current ]]; then
    protect=$(basename "$(readlink -f /opt/mag-stack/current)")
  fi
  if [[ -n "$protect" ]]; then
    for id in "${verified[@]}"; do
      [[ -f "$MAG_BACKUP_ROOT/$id/META" ]] || continue
      grep -Fq "kind=deploy" "$MAG_BACKUP_ROOT/$id/META" || continue
      grep -Fq "release=$protect" "$MAG_BACKUP_ROOT/$id/META" || continue
      keep["$id"]=1
    done
  fi
  for id in "${verified[@]}"; do
    [[ -n "${keep[$id]:-}" ]] && continue
    echo "Removing old full backup $id"
    rm -rf -- "$MAG_BACKUP_ROOT/$id"
  done
}
rotate_full_backups
repo_bytes=$(du -sb "$run_dir" | awk '{print $1}')
finished=$(date +%s)
status_update --arg id "$snapshot_id" --arg kind "$kind" --arg archive "$(basename "$run_dir")" \
  --argjson at "$finished" --argjson bytes "$bytes" --argjson repo_bytes "$repo_bytes" \
  --argjson seconds "$((finished-started))" --argjson offsite "$offsite" \
  '.last_success=$at | .last_snapshot=$id | .last_archive=$archive | .last_kind=$kind | .last_bytes=$bytes | .repo_bytes=$repo_bytes | .last_duration_sec=$seconds | .offsite_ok=$offsite | .last_error="" | .healthy=true'
logger -t mag-backup "OK kind=$kind id=$snapshot_id bytes=$bytes seconds=$((finished-started)) offsite=$offsite archive=$(basename "$run_dir")" 2>/dev/null || :
printf 'Backup %s: %s bytes=%s duration=%ss offsite=%s archive=%s\n' \
  "$kind" "$snapshot_id" "$bytes" "$((finished-started))" "$offsite" "$(basename "$run_dir")"
