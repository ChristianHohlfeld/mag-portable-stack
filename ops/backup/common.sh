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
MAG_BACKUP_OFFSITE_ENABLED="${MAG_BACKUP_OFFSITE_ENABLED:-0}"
if [[ -f "$MAG_BACKUP_CONFIG/config.env" ]]; then
  [[ ! -L "$MAG_BACKUP_CONFIG/config.env" ]] || { echo "Symlink config refused" >&2; exit 1; }
  # shellcheck disable=SC1091
  source "$MAG_BACKUP_CONFIG/config.env"
fi
# Offsite must be opted in explicitly; a residual aws.env must NEVER
# trigger an S3 request while AWS is administratively stopped.
MAG_BACKUP_OFFSITE_ENABLED="${MAG_BACKUP_OFFSITE_ENABLED:-0}"
case "$MAG_BACKUP_OFFSITE_ENABLED" in 0|1) ;; *)
 echo 'MAG_BACKUP_OFFSITE_ENABLED must be exactly 0 or 1' >&2; exit 2;;
esac
# Empty-password repo explicitly approved for on-host local-only use.
# This disables *effective confidentiality*: filesystem ACLs are mandatory.
# Every restic call must pass the flag; no secret, stdin, env or prompt.
unset RESTIC_PASSWORD RESTIC_PASSWORD_FILE RESTIC_PASSWORD_COMMAND RESTIC_KEY_HINT
export RESTIC_REPOSITORY="$MAG_BACKUP_REPO"
command -v restic >/dev/null
restic --help | grep -Fq -- '--insecure-no-password' ||
  { echo 'restic >=0.17 required (--insecure-no-password)' >&2; exit 1; }
restic() { command restic --insecure-no-password "$@"; }
command -v jq >/dev/null
command -v flock >/dev/null
mkdir -p "$(dirname "$MAG_BACKUP_LOCK")" "$(dirname "$MAG_BACKUP_STATUS")" "$MAG_BACKUP_WORK"
# Never use a non-root-owned or group/world-readable archive root.
if [[ "${MAG_BACKUP_TEST_MODE:-0}" != 1 && -e "$MAG_BACKUP_REPO" ]]; then
  [[ -d "$MAG_BACKUP_REPO" && ! -L "$MAG_BACKUP_REPO" ]] ||
    { echo "Unsafe backup path $MAG_BACKUP_REPO" >&2; exit 1; }
  [[ "$(stat -c %u "$MAG_BACKUP_REPO")" == 0 ]] ||
    { echo "Backup repository must be root-owned ($MAG_BACKUP_REPO)" >&2; exit 1; }
  mode=$(stat -c %a "$MAG_BACKUP_REPO")
  [[ "$mode" == 700 || "$mode" == 0700 ]] ||
    { echo "Backup repository must be inaccessible to group/other ($MAG_BACKUP_REPO)" >&2; exit 1; }
fi
# Owner-visible file, staging inaccessible.
chmod 0700 "$MAG_BACKUP_WORK"
chmod 0711 "$(dirname "$MAG_BACKUP_STATUS")"
# Only a deliberate opt-in enables S3. Creds alone are NEVER consent.
s3_enabled() { [[ "$MAG_BACKUP_OFFSITE_ENABLED" == 1 ]]; }
load_aws() {
  s3_enabled || { echo 'AWS offsite disabled by config' >&2; return 1; }
  [[ -s "$MAG_BACKUP_CONFIG/aws.env" && ! -L "$MAG_BACKUP_CONFIG/aws.env" ]] ||
    { echo 'Offsite enabled but AWS credentials missing/unsafe' >&2; return 1; }
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
