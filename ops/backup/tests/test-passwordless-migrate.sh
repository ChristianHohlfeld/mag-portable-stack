#!/usr/bin/env bash
# Real old-key -> empty-key migration; exercises actual restic >=0.17.
set -euo pipefail
root="$(cd "$(dirname "$0")/../../.." && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf -- "$tmp"' EXIT
mkdir -p "$tmp/repo" "$tmp/data"
printf 'KEEP_THIS_BEFORE_KEY_MIGRATION\n' > "$tmp/data/precious.txt"
printf 'temporary-fixture-password-not-from-mag\n' > "$tmp/legacy-pw"
chmod 0600 "$tmp/legacy-pw"
unset RESTIC_PASSWORD RESTIC_PASSWORD_FILE RESTIC_PASSWORD_COMMAND
command restic --help | grep -Fq -- '--insecure-no-password'
command restic --repo "$tmp/repo" --password-file "$tmp/legacy-pw" init
command restic --repo "$tmp/repo" --password-file "$tmp/legacy-pw" backup "$tmp/data"
before="$(command restic --repo "$tmp/repo" --password-file "$tmp/legacy-pw" snapshots --json | jq -r '.[0].id')"
sudo /usr/bin/env MAG_BACKUP_REPO="$tmp/repo" MAG_BACKUP_LEGACY_PASSWORD_FILE="$tmp/legacy-pw" \
  bash "$root/ops/backup/migrate-passwordless.sh"
after="$(command restic --repo "$tmp/repo" --insecure-no-password snapshots --json | jq -r '.[0].id')"
[[ "$after" == "$before" ]] || { echo 'Snapshot ID mutated' >&2; exit 1; }
command restic --repo "$tmp/repo" --insecure-no-password key list --json |
  jq -e 'length == 1 and .[0].id != ""' >/dev/null
command restic --repo "$tmp/repo" --insecure-no-password restore latest --target "$tmp/restored"
grep -Rq KEEP_THIS_BEFORE_KEY_MIGRATION "$tmp/restored"
test -f "$tmp/legacy-pw" # old file still under operator control
rm -f "$tmp/legacy-pw"
command restic --repo "$tmp/repo" --insecure-no-password check
echo 'Restic prior snapshots recovered, old key removed, zero password prompts: PASS'
