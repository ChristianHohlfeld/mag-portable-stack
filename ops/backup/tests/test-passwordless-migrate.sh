#!/usr/bin/env bash
# mag-test-timeout: 180
if [ -z "${MAG_TEST_DEADLINE_ARMED:-}" ]; then
  export MAG_TEST_DEADLINE_ARMED=1
  exec timeout --kill-after=5s 180s bash "$0" "$@"
fi
# Real old-key -> empty-key migration; exercises actual restic >=0.17.
set -euo pipefail
root="$(cd "$(dirname "$0")/../../.." && pwd)"
tmp="$(mktemp -d)"
trap 'sudo rm -rf -- "$tmp"' EXIT
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
# The migration runs as root and intentionally creates root-only key files.
# Do not make the real repository world-readable to make CI pass.
after="$(sudo restic --repo "$tmp/repo" --insecure-no-password snapshots --json | jq -r '.[0].id')"
[[ "$after" == "$before" ]] || { echo 'Snapshot ID mutated' >&2; exit 1; }
sudo restic --repo "$tmp/repo" --insecure-no-password key list --json |
  jq -e 'length == 1 and .[0].id != ""' >/dev/null
sudo restic --repo "$tmp/repo" --insecure-no-password restore latest --target "$tmp/restored"
sudo grep -Rq KEEP_THIS_BEFORE_KEY_MIGRATION "$tmp/restored"
test -f "$tmp/legacy-pw" # old file still under operator control
rm -f "$tmp/legacy-pw"
sudo restic --repo "$tmp/repo" --insecure-no-password check
echo 'Restic prior snapshots recovered, old key removed, zero password prompts: PASS'
