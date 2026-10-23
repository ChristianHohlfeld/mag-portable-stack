#!/usr/bin/env bash
# Shared root-only configuration. Local restic only; there is no offsite/S3 path.
set -euo pipefail
umask 077
MAG_BACKUP_CONFIG="${MAG_BACKUP_CONFIG:-/etc/mag-backup}"
# Each run writes a new directory under this root. There is no shared
# deduplicated chain. MAG_BACKUP_REPO remains only for the legacy repo.
MAG_BACKUP_ROOT="${MAG_BACKUP_ROOT:-/var/backups/mag-full}"
MAG_BACKUP_REPO="${MAG_BACKUP_REPO:-/var/backups/mag-restic}"
MAG_BACKUP_WORK="${MAG_BACKUP_WORK:-/var/lib/mag-backup/work}"
MAG_BACKUP_SOURCE="${MAG_BACKUP_SOURCE:-/}"
MAG_BACKUP_STATUS="${MAG_BACKUP_STATUS:-/var/lib/mag-backup/status.json}"
MAG_BACKUP_LOCK="${MAG_BACKUP_LOCK:-/run/lock/mag-backup.lock}"
if [[ -f "$MAG_BACKUP_CONFIG/config.env" ]]; then
  [[ ! -L "$MAG_BACKUP_CONFIG/config.env" ]] || { echo "Symlink config refused" >&2; exit 1; }
  # shellcheck disable=SC1091
  source "$MAG_BACKUP_CONFIG/config.env"
fi
# S3 offsite was removed for good. A stale opt-in fails closed so nobody
# believes an offsite copy exists. A residual aws.env is never read.
if [[ "${MAG_BACKUP_OFFSITE_ENABLED:-0}" != 0 ]]; then
  echo 'S3 offsite was removed; set MAG_BACKUP_OFFSITE_ENABLED=0 or delete the line' >&2
  exit 2
fi
if [[ -e "$MAG_BACKUP_CONFIG/aws.env" || -L "$MAG_BACKUP_CONFIG/aws.env" ]]; then
  echo "Note: $MAG_BACKUP_CONFIG/aws.env is unused (S3 offsite removed) and may be deleted" >&2
fi
# Empty-password repo explicitly approved for on-host local-only use.
# This disables *effective confidentiality*: filesystem ACLs are mandatory.
# Every restic call must pass the flag; no secret, stdin, env or prompt.
unset RESTIC_PASSWORD RESTIC_PASSWORD_FILE RESTIC_PASSWORD_COMMAND RESTIC_KEY_HINT
# Prefer the pinned binary. apt restic 0.16.4 does not support passwordless mode.
if [[ -x /usr/local/bin/restic ]]; then
  MAG_RESTIC_BIN=/usr/local/bin/restic
else
  command -v restic >/dev/null
  MAG_RESTIC_BIN=$(command -v restic)
fi
"$MAG_RESTIC_BIN" --help | grep -Fq -- '--insecure-no-password' ||
  { echo 'restic >=0.17 required (--insecure-no-password)' >&2; exit 1; }
restic() { command "$MAG_RESTIC_BIN" --insecure-no-password "$@"; }
# Passwordless restic is readable by anyone with filesystem access.
# Never use a non-root-owned or group/world-readable archive root.
if [[ "${MAG_BACKUP_TEST_MODE:-0}" != 1 && -e "$MAG_BACKUP_ROOT" ]]; then
  [[ -d "$MAG_BACKUP_ROOT" && ! -L "$MAG_BACKUP_ROOT" ]] ||
    { echo "Unsafe backup path $MAG_BACKUP_ROOT" >&2; exit 1; }
  [[ "$(stat -c %u "$MAG_BACKUP_ROOT")" == 0 ]] ||
    { echo "Backup repository must be root-owned ($MAG_BACKUP_ROOT)" >&2; exit 1; }
  mode=$(stat -c %a "$MAG_BACKUP_ROOT")
  (( (8#$mode & 077) == 0 )) ||
    { echo "Backup repository must be inaccessible to group/other ($MAG_BACKUP_ROOT)" >&2; exit 1; }
fi
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
