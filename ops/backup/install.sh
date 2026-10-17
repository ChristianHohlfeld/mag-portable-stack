#!/usr/bin/env bash
# Prepare on host after merge, then smoke-test before retiring old timers.
set -euo pipefail
[[ $EUID == 0 ]] || { echo 'Root required' >&2; exit 1; }
here=$(cd -- "$(dirname -- "$0")" && pwd)
for cmd in restic jq flock sqlite3 numactl nice ionice openssl systemctl; do
 command -v "$cmd" >/dev/null || { echo "Missing dependency: $cmd" >&2; exit 1; }
done
numactl -H | grep -Eq '^available: 2 nodes \(0-1\)' || {
 echo 'NUMA node 1 must exist; refuse to bind to node 0' >&2; exit 1;
}
[[ ! -e /etc/mag-backup || ( -d /etc/mag-backup && ! -L /etc/mag-backup ) ]] || exit 1
install -d -m 0700 -o root -g root /etc/mag-backup /var/lib/mag-backup/work /var/backups/mag-restic
install -d -m 0711 -o root -g root /var/lib/mag-backup
if [[ ! -e /etc/mag-backup/restic-password ]]; then
  openssl rand -hex 32 > /etc/mag-backup/restic-password
fi
chown root:root /etc/mag-backup/restic-password
chmod 0600 /etc/mag-backup/restic-password
if [[ -e /etc/mag-backup/config.env ]]; then
  [[ -f /etc/mag-backup/config.env && ! -L /etc/mag-backup/config.env ]] || exit 1
  chmod 0600 /etc/mag-backup/config.env
fi
# Never rotate the key automatically; operator keeps an offline copy.
if [[ -e /etc/mag-backup/aws.env ]]; then
  [[ -f /etc/mag-backup/aws.env && ! -L /etc/mag-backup/aws.env ]] || exit 1
  chmod 0600 /etc/mag-backup/aws.env
fi
install -d -m 0755 /usr/local/lib/mag-backup
for name in common.sh backup.sh restore.sh maintenance.sh; do
  install -m 0755 "$here/$name" "/usr/local/lib/mag-backup/$name"
done
for unit in "$here"/units/*.service "$here"/units/*.timer; do
  install -m 0644 "$unit" "/etc/systemd/system/$(basename "$unit")"
done
install -m 0440 "$here/units/mag-backup-clear.sudoers" /etc/sudoers.d/mag-backup-clear
# Clear timer is deliberately NOT enabled at boot; each owner clear schedules
# a one-shot countdown with systemctl --no-block restart, collapsing bursts.
# The ordinary daily, weekly and monitoring timers are unchanged.
visudo -cf /etc/sudoers.d/mag-backup-clear >/dev/null
systemctl daemon-reload
# Snapshot before old Gitea timer is disabled: no protection gap.
# This may take time but must succeed before the cutover is announced.
"$here/backup.sh" daily
systemctl enable --now mag-backup-daily.timer mag-backup-weekly.timer \
 mag-backup-monthly.timer mag-backup-restore-test.timer mag-backup-monitor.timer
if systemctl cat gitea-backup.timer >/dev/null 2>&1; then
  systemctl disable --now gitea-backup.timer
fi
echo 'Local MAG backup active. Old /etc/mag-dr and /var/backups/restic preserved read-only for manual retirement.'
echo 'Save /etc/mag-backup/restic-password OFFLINE. Optional S3: configure /etc/mag-backup/aws.env (0600).'
