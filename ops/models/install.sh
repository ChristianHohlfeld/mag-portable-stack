#!/usr/bin/env bash
# Install the model layer (run as root, from deploy.sh or by hand):
#   mag-modeld            supervisor: download watch, verify, start
#   mag-model@<bot>       CPU-only llama.cpp server (lena :8191, noah :8192)
#   mag-model-fetch@<bot> resumable download
# Never waits for a model, never restarts a running one and never deletes a
# model file. Retired model units are stopped and their unit files moved to
# /var/backups/mag-legacy-units (the GGUFs stay where they are).
set -euo pipefail
[[ $EUID == 0 ]] || { echo 'run as root' >&2; exit 1; }
SOURCE="$(cd -- "$(dirname -- "$0")/../.." && pwd)"
BIN="${MAG_MODELD_BIN:-$SOURCE/build/mag-modeld}"
[[ -x $BIN ]] || BIN=/opt/mag-stack/current/bin/mag-modeld
[[ -x $BIN ]] || { echo 'mag-modeld binary missing (build first)' >&2; exit 1; }
LIB=/usr/local/lib/mag-models
# shellcheck source=../host.sh
source "$SOURCE/ops/host.sh"
STATE=$(jq -r '.state_dir' "$SOURCE/ops/models/models.json")
[[ $STATE == /var/lib/* ]] || { echo 'models.json: state_dir must be under /var/lib' >&2; exit 1; }
jq -e '.bots | length > 0' "$SOURCE/ops/models/models.json" >/dev/null

# --- files ---------------------------------------------------------------
install -d -m 0755 "$LIB"
install -d -m 0755 "$STATE"
install -d -m 0755 -o "$MAG_OWNER" -g "$MAG_OWNER" "$STATE/slots"
# The room (magbot) drops control verbs here; it cannot list or read others.
if getent group magbot >/dev/null; then
  install -d -m 0730 -o root -g magbot "$STATE/control"
else
  install -d -m 0700 "$STATE/control"
fi
for f in run.sh fetch.sh build-draft.sh; do install -m 0755 "$SOURCE/ops/models/$f" "$LIB/$f"; done
# Paths and the unit's user follow this host's owner (ops/host.env).
mag_render "$SOURCE/ops/models/models.json" > "$LIB/models.json.next"
chmod 0644 "$LIB/models.json.next"
mv -f "$LIB/models.json.next" "$LIB/models.json"
install -m 0755 "$BIN" "$LIB/mag-modeld.next"
mv -f "$LIB/mag-modeld.next" "$LIB/mag-modeld"
for unit in mag-modeld.service mag-model@.service mag-model-fetch@.service; do
  mag_render "$SOURCE/ops/models/$unit" > "/etc/systemd/system/$unit.next"
  chmod 0644 "/etc/systemd/system/$unit.next"
  mv -f "/etc/systemd/system/$unit.next" "/etc/systemd/system/$unit"
done
systemctl daemon-reload
systemctl enable mag-modeld.service >/dev/null
# The supervisor is stateless: restarting it never touches a running model.
systemctl restart mag-modeld.service
echo 'Model layer installed: mag-modeld supervises lena (:8191) and noah (:8192)'
