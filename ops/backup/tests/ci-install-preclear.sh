#!/usr/bin/env bash
# Github Actions ONLY: actual encrypted restic snapshot of the native fixture.
set -euo pipefail
[[ "${GITHUB_ACTIONS:-}" == true && "${CI:-}" == true ]] || {
  echo "CI-only installer refused outside GitHub Actions" >&2; exit 4;
}
root=$(cd "$(dirname "$0")/../../.." && pwd)
[[ -f "$root/ops/backup/backup.sh" ]] || exit 4
base=/tmp/mag-backup-ci
sudo install -d -m 0755 "$base" "$base/config" "$base/source" "$base/run" "$base/status" "$base/work"
# Native unprivileged fixture must publish the per-test room path. This is
# restricted to the CI marker; the encrypted repo/config remains root-owned.
sudo chown "$(id -u):$(id -g)" "$base"
if [[ ! -f "$base/config/restic-password" ]]; then
  printf '%s\n' "temporary-disposable-actions-fixture-password" | sudo tee "$base/config/restic-password" >/dev/null
fi
sudo chmod 0600 "$base/config/restic-password"
sudo tee /etc/systemd/system/mag-backup-clear.service >/dev/null <<EOF
[Unit]
Description=CI fixture - actual encrypted restic snapshot before room clear
[Service]
Type=oneshot
Environment=GITHUB_ACTIONS=true
Environment=MAG_BACKUP_TEST_MODE=1
Environment=MAG_BACKUP_CONFIG=$base/config
Environment=MAG_BACKUP_SOURCE=$base/source
Environment=MAG_BACKUP_REPO=$base/repo
Environment=MAG_BACKUP_WORK=$base/work
Environment=MAG_BACKUP_LOCK=$base/run/backup.lock
Environment=MAG_BACKUP_STATUS=$base/status/status.json
ExecStart=/usr/bin/bash $root/ops/backup/tests/ci-clear-oneshot.sh
TimeoutStartSec=120
EOF
sudo systemctl daemon-reload
sudo systemctl reset-failed mag-backup-clear.service || :
echo "CI: real encrypted restic oneshot installed"
