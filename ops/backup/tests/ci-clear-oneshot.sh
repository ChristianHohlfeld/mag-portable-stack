#!/usr/bin/env bash
# mag-test-timeout: 180
if [ -z "${MAG_TEST_DEADLINE_ARMED:-}" ]; then
  export MAG_TEST_DEADLINE_ARMED=1
  exec timeout --kill-after=5s 180s bash "$0" "$@"
fi
# Fixture-only manually invoked backup; CI room-clear no longer invokes it.
set -euo pipefail
[[ "${GITHUB_ACTIONS:-}" == true && "${MAG_BACKUP_TEST_MODE:-}" == 1 ]] || exit 4
root=$(cd "$(dirname "$0")/../../.." && pwd)
base=/tmp/mag-backup-ci
[[ -f "$base/state-path" && ! -L "$base/state-path" ]] || exit 4
state=$(cat "$base/state-path")
[[ "$state" == /tmp/* && -d "$state" && ! -L "$state" ]] || exit 4
dst="$MAG_BACKUP_SOURCE/var/lib/magbot/room"
mkdir -p "$(dirname "$dst")"
rm -rf -- "$dst"
mkdir -p "$dst"
cp -a --reflink=auto -- "$state/." "$dst/"
exec "$root/ops/backup/backup.sh" clear
