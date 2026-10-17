#!/usr/bin/env bash
# One-time, non-destructive transition. Ali removes old password file only
# after successful check + temp restore. Never auto-rotate/reinitialize.
set -euo pipefail
[[ $EUID == 0 ]] || { echo 'Run as root' >&2; exit 1; }
repo=${MAG_BACKUP_REPO:-/var/backups/mag-restic}
pw=${MAG_BACKUP_LEGACY_PASSWORD_FILE:-/etc/mag-backup/restic-password}
[[ -d "$repo" && ! -L "$repo" ]] || { echo 'Restic repository missing; new one may be initialized passwordlessly' >&2; exit 1; }
[[ -f "$repo/config" ]] || { echo 'Not an existing Restic repository' >&2; exit 1; }
export RESTIC_REPOSITORY="$repo"
unset RESTIC_PASSWORD RESTIC_PASSWORD_FILE RESTIC_PASSWORD_COMMAND RESTIC_KEY_HINT
command restic --help | grep -Fq -- '--insecure-no-password' || { echo 'restic >=0.17 required' >&2; exit 1; }
# Already converted; old password can be deleted only after this check.
if command restic --insecure-no-password snapshots --json >/dev/null 2>&1; then
  echo 'Passwordless key already works; verifying integrity and restore.'
else
  [[ -f "$pw" && ! -L "$pw" && -s "$pw" ]] || { echo 'Legacy password file unavailable; recovery requires it' >&2; exit 1; }
  old="$(command restic --password-file "$pw" key list --json)"
  mapfile -t ids < <(jq -r '.[].id' <<<"$old")
  (( ${#ids[@]} > 0 )) || exit 1
  command restic --password-file "$pw" key add --new-insecure-no-password
  command restic --insecure-no-password snapshots --json >/dev/null || {
    echo 'New key unreadable; old keys untouched' >&2; exit 1;
  }
  # Each ID here was present BEFORE creation; retain the newly added key.
  for id in "${ids[@]}"; do
    [[ "$id" =~ ^[a-f0-9]{64}$ ]] || exit 1
    command restic --insecure-no-password key remove "$id"
  done
fi
command restic --insecure-no-password check
tmp="$(mktemp -d /var/tmp/mag-restic-migration.XXXXXXXX)"
trap 'rm -rf -- "$tmp"' EXIT
snapshots="$(command restic --insecure-no-password snapshots --json)"
if jq -e 'length>0' <<<"$snapshots" >/dev/null; then
  command restic --insecure-no-password restore latest --target "$tmp"
  test -n "$(find "$tmp" -type f -print -quit)"
fi
echo 'PASS: old data preserved; passwordless key usable; integrity/restore checked.'
echo "Ali may now remove legacy password file $pw and any offline copies; do not log it."
