#!/usr/bin/env bash
# Code-only update. Does not invoke apt, restart libvirt, or modify VM images/networks.
set -Eeuo pipefail
if [[ $EUID -ne 0 ]]; then echo 'Run: sudo bash upgrade-magbot.sh' >&2; exit 1; fi
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
[[ -f "$HERE/magbot.py" ]] || { echo 'Missing magbot.py' >&2; exit 1; }
python3 -m py_compile "$HERE/magbot.py"
python3 -m unittest discover -s "$HERE/tests" -q
bash -n "$HERE/smoke-test.sh" "$HERE/install-magbot.sh"
[[ -x /usr/local/libexec/magbot ]] || { echo 'MAGBOT is not yet installed; use install-magbot.sh first' >&2; exit 1; }
[[ -e /dev/kvm ]] || { echo '/dev/kvm missing; refusing upgrade' >&2; exit 1; }
BACKUP="/usr/local/libexec/magbot.backup.$(date +%Y%m%d-%H%M%S).$$"
cp -a /usr/local/libexec/magbot "$BACKUP"
install -m 0755 "$HERE/magbot.py" /usr/local/libexec/magbot
if ! /usr/local/bin/magbot doctor; then
  echo "MAGBOT doctor failed. Restoring old binary: $BACKUP" >&2
  install -m 0755 "$BACKUP" /usr/local/libexec/magbot
  exit 1
fi
printf 'MAGBOT code updated. Backup: %s\n' "$BACKUP"
printf 'No host packages, VMs, images, libvirt networks or system services were changed.\n'
