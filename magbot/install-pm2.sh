#!/usr/bin/env bash
# PM2 supervises the read-only MAGBOT watchdog, NEVER the VMs directly.
set -euo pipefail
if [[ "$EUID" -eq 0 ]]; then
  echo 'Do not run this installer as root. Use: bash install-pm2.sh' >&2
  exit 1
fi
DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
USER_NAME="$(id -un)"
[[ "$USER_NAME" =~ ^[a-z_][a-z0-9_-]*$ ]] || { echo 'Unsupported username' >&2; exit 1; }
[[ -x /usr/local/bin/magbot ]] || { echo 'MAGBOT missing: first run sudo bash install-magbot.sh' >&2; exit 1; }
[[ -e /dev/kvm ]] || { echo 'KVM not available' >&2; exit 1; }
for cmd in node npm sudo; do command -v "$cmd" >/dev/null || {
  if [[ "$cmd" == node || "$cmd" == npm ]] && [[ -x "$HOME/node-v20.18.0-linux-x64/bin/$cmd" ]]; then
    export PATH="$HOME/node-v20.18.0-linux-x64/bin:$PATH"
  else
    echo "$cmd missing; install Node.js/npm and retry" >&2; exit 1
  fi
}; done
if ! command -v pm2 >/dev/null 2>&1; then
  echo 'Installing PM2 into a user-owned prefix, without touching global PM2 installations.'
  npm install --global --prefix "$HOME/.local/magbot-node" 'pm2@6'
  export PATH="$HOME/.local/magbot-node/bin:$PATH"
fi
PM2_BIN="$(command -v pm2)"
[[ -f "$DIR/ecosystem.config.cjs" ]] || { echo 'Missing ecosystem.config.cjs' >&2; exit 1; }
# Passwordless privileges intentionally restricted to two read-only exact commands.
# Never authorize spawn/despawn, generic virsh or arbitrary command args.
sudo -v
EXE="$(readlink -f /usr/local/bin/magbot)"
[[ "$(stat -c '%U' "$EXE")" == root ]] || { echo 'Unsafe magbot executable ownership' >&2; exit 1; }
TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT
printf '%s ALL=(root) NOPASSWD: /usr/local/bin/magbot status\n' "$USER_NAME" >"$TMP"
sudo visudo -cf "$TMP" >/dev/null
sudo install -m 0440 "$TMP" "/etc/sudoers.d/magbot-pm2-$USER_NAME"
sudo -n /usr/local/bin/magbot status >/dev/null
if "$PM2_BIN" describe magbot-watch >/dev/null 2>&1; then
  "$PM2_BIN" restart magbot-watch --update-env
else
  "$PM2_BIN" start "$DIR/ecosystem.config.cjs" --only magbot-watch
fi
"$PM2_BIN" save
# This setup creates a systemd unit for the unprivileged PM2 user, without
# allowing it to manage libvirt directly. Avoid replacing existing PM2 apps.
sudo env PATH="$PATH" "$PM2_BIN" startup systemd -u "$USER_NAME" --hp "$HOME"
if ! systemctl is-enabled --quiet "pm2-$USER_NAME.service"; then
  echo "WARNING: pm2-$USER_NAME not enabled; inspect the PM2 startup output." >&2
  exit 1
fi
echo '=== PM2 status ==='
"$PM2_BIN" list
printf '\nPM2 watcher installed with reboot persistence.\n'
printf 'VMs are persisted separately by libvirt; run sudo magbot autostart-enable after smoke-test completion.\n'
printf 'PM2 binary: %s (use this full path if pm2 is not on your login PATH).\n' "$PM2_BIN"
