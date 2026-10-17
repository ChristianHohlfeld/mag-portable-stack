#!/usr/bin/env bash
# Installs and enables the update timer. Run as root after a successful deploy
# of the current checkout (ops/update.sh does this). Disable with:
#   sudo touch /etc/mag-stack/auto-update.disabled
#   sudo systemctl disable --now mag-auto-update.timer
set -euo pipefail
[[ $EUID == 0 ]] || { echo 'Root required' >&2; exit 1; }
here="$(cd -- "$(dirname "$0")" && pwd)"
ROOT="$(cd -- "$here/../.." && pwd)"
UNITS_ONLY=0
[[ "${1:-}" == --units-only ]] && UNITS_ONLY=1
install -d -m 0755 /var/lib/mag-stack/auto-update /etc/mag-stack
if (( ! UNITS_ONLY )); then
  runuser -u "$(stat -c %U "$ROOT")" -- git -C "$ROOT" rev-parse HEAD > /var/lib/mag-stack/auto-update/deployed-sha
  rm -f /var/lib/mag-stack/auto-update/failed-sha /etc/mag-stack/auto-update.disabled
fi
sed "s#@ROOT@#$ROOT#g" "$here/mag-auto-update.service" > /etc/systemd/system/mag-auto-update.service
install -m 0644 "$here/mag-auto-update.timer" /etc/systemd/system/mag-auto-update.timer
systemctl daemon-reload
systemctl enable --now mag-auto-update.timer
