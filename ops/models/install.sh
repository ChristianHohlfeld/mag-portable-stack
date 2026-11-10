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
STATE=$(jq -r '.state_dir' "$SOURCE/ops/models/models.json")
[[ $STATE == /var/lib/* ]] || { echo 'models.json: state_dir must be under /var/lib' >&2; exit 1; }
jq -e '.bots | length > 0' "$SOURCE/ops/models/models.json" >/dev/null

# --- retire every earlier model unit (ports 8191/8192 belong to mag-model@) --
legacy=()
while read -r unit _; do
  [[ $unit =~ ^mag-(llm|model-gate|model-pin)[A-Za-z0-9@._-]*\.(service|timer)$ ]] && legacy+=("$unit")
done < <(systemctl list-units --all --plain --no-legend 'mag-llm*' 'mag-model-gate*' 'mag-model-pin*' 2>/dev/null; \
         systemctl list-unit-files --plain --no-legend 'mag-llm*' 'mag-model-gate*' 'mag-model-pin*' 2>/dev/null)
if (( ${#legacy[@]} )); then
  mapfile -t legacy < <(printf '%s\n' "${legacy[@]}" | sort -u)
  for unit in "${legacy[@]}"; do
    [[ $unit == *@.service ]] && continue
    systemctl unmask --runtime "$unit" >/dev/null 2>&1 || :
    timeout 150 systemctl disable --now "$unit" >/dev/null 2>&1 ||
      timeout 60 systemctl kill --signal=SIGKILL "$unit" >/dev/null 2>&1 || :
    systemctl reset-failed "$unit" >/dev/null 2>&1 || :
  done
fi
# Not below /var/backups/mag-stack: prune-releases.sh owns that directory.
attic=/var/backups/mag-legacy-units
shopt -s nullglob
for f in /etc/systemd/system/mag-llm*.service /etc/systemd/system/mag-llm*.service.d \
         /etc/systemd/system/mag-model-gate.* /etc/systemd/system/mag-model-pin.*; do
  install -d -m 0700 "$attic"
  rm -rf "${attic:?}/$(basename "$f")"
  mv "$f" "$attic/"
done
shopt -u nullglob

# KV dumps of the retired servers hold old conversations and can never be
# loaded again (other models, other slot directory): remove them. Weights are
# elsewhere and are not touched.
if [[ -d /var/lib/mag-llm/slots ]]; then
  find /var/lib/mag-llm/slots -xdev -type f -name '*.bin' -delete 2>/dev/null || :
fi

# --- files ---------------------------------------------------------------
install -d -m 0755 "$LIB"
install -d -m 0755 "$STATE"
install -d -m 0755 -o chris -g chris "$STATE/slots"
# The room (magbot) drops control verbs here; it cannot list or read others.
if getent group magbot >/dev/null; then
  install -d -m 0730 -o root -g magbot "$STATE/control"
else
  install -d -m 0700 "$STATE/control"
fi
for f in run.sh fetch.sh; do install -m 0755 "$SOURCE/ops/models/$f" "$LIB/$f"; done
install -m 0644 "$SOURCE/ops/models/models.json" "$LIB/models.json.next"
mv -f "$LIB/models.json.next" "$LIB/models.json"
install -m 0755 "$BIN" "$LIB/mag-modeld.next"
mv -f "$LIB/mag-modeld.next" "$LIB/mag-modeld"
for unit in mag-modeld.service mag-model@.service mag-model-fetch@.service; do
  install -m 0644 "$SOURCE/ops/models/$unit" "/etc/systemd/system/$unit"
done
systemctl daemon-reload
systemctl enable mag-modeld.service >/dev/null
# The supervisor is stateless: restarting it never touches a running model.
systemctl restart mag-modeld.service
echo 'Model layer installed: mag-modeld supervises lena (:8191) and noah (:8192)'
