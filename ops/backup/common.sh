#!/usr/bin/env bash
# Shared root-only configuration. Never echo passwords or AWS credentials.
set -euo pipefail
umask 077
MAG_BACKUP_CONFIG="${MAG_BACKUP_CONFIG:-/etc/mag-backup}"
MAG_BACKUP_REPO="${MAG_BACKUP_REPO:-/var/backups/mag-restic}"
MAG_BACKUP_WORK="${MAG_BACKUP_WORK:-/var/lib/mag-backup/work}"
MAG_BACKUP_SOURCE="${MAG_BACKUP_SOURCE:-/}"
MAG_BACKUP_STATUS="${MAG_BACKUP_STATUS:-/var/lib/mag-backup/status.json}"
MAG_BACKUP_LOCK="${MAG_BACKUP_LOCK:-/run/lock/mag-backup.lock}"
MAG_BACKUP_BUCKET="${MAG_BACKUP_BUCKET:-mag-backup-595505951010-euc1}"
if [[ -f "$MAG_BACKUP_CONFIG/config.env" ]]; then
  [[ ! -L "$MAG_BACKUP_CONFIG/config.env" ]] || { echo "Symlink config refused" >&2; exit 1; }
  # shellcheck disable=SC1091
  source "$MAG_BACKUP_CONFIG/config.env"
fi
export RESTIC_PASSWORD_FILE="${RESTIC_PASSWORD_FILE:-$MAG_BACKUP_CONFIG/restic-password}"
export RESTIC_REPOSITORY="$MAG_BACKUP_REPO"
[[ -r "$RESTIC_PASSWORD_FILE" ]] || { echo 'Missing restic password file' >&2; exit 1; }
command -v restic >/dev/null
command -v jq >/dev/null
command -v flock >/dev/null
mkdir -p "$(dirname "$MAG_BACKUP_LOCK")" "$(dirname "$MAG_BACKUP_STATUS")" "$MAG_BACKUP_WORK"
# Owner-visible file, staging inaccessible.
chmod 0700 "$MAG_BACKUP_WORK"
chmod 0711 "$(dirname "$MAG_BACKUP_STATUS")"
# Test for the presence of a credential file, not an AWS network probe.
s3_enabled() { [[ -s "$MAG_BACKUP_CONFIG/aws.env" && ! -L "$MAG_BACKUP_CONFIG/aws.env" ]]; }
load_aws() {
  s3_enabled || return 1
  # shellcheck disable=SC1091
  source "$MAG_BACKUP_CONFIG/aws.env"
  export AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN AWS_DEFAULT_REGION
  : "${AWS_DEFAULT_REGION:=eu-central-1}"
  [[ "$AWS_DEFAULT_REGION" == eu-central-1 ]] || { echo 'Wrong S3 region' >&2; return 1; }
  command -v aws >/dev/null
}
status_update() {
  local tmp
  tmp=$(mktemp "$(dirname "$MAG_BACKUP_STATUS")/.status.XXXXXXXX")
  if [[ -f "$MAG_BACKUP_STATUS" ]] && jq -e 'type=="object"' "$MAG_BACKUP_STATUS" >/dev/null 2>&1; then
    jq "$@" "$MAG_BACKUP_STATUS" > "$tmp"
  else
    printf '{}\n' | jq "$@" > "$tmp"
  fi
  chmod 0644 "$tmp"
  mv -f "$tmp" "$MAG_BACKUP_STATUS"
}
check_root() {
  [[ $EUID == 0 || "${MAG_BACKUP_TEST_MODE:-0}" == 1 ]] || { echo 'Root required' >&2; exit 1; }
}
backup_lock() {
  exec 9>"$MAG_BACKUP_LOCK"
  flock -w "${MAG_BACKUP_LOCK_WAIT:-120}" 9 || { echo 'Backup already in progress' >&2; exit 75; }
}
