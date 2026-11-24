#!/usr/bin/env bash
# Install the model layer (run as root, from deploy.sh or by hand):
#   mag-modeld            supervisor: download watch, verify, start
#   mag-model@<bot>       Dedicated llama.cpp server per bot
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
for f in run.sh run-mi50.sh gpu-guard.py mi50-mtp.py fetch.sh build-draft.sh; do install -m 0755 "$SOURCE/ops/models/$f" "$LIB/$f"; done
# Paths and the unit's user follow this host's owner (ops/host.env).
# A software deploy must not silently change local model selection.
# Replace the retired uncensored default once, or on explicit operator request.
if [[ ! -f "$LIB/models.json" || ${MAG_REPLACE_MODEL_CONFIG:-0} == 1 ]] || jq -e '.bots | to_entries | any((.value.model.path // "") | contains("/gemma4-26b-a4b-uncensored/"))' "$LIB/models.json" >/dev/null; then
  mag_render "$SOURCE/ops/models/models.json" > "$LIB/models.json.next"
  chmod 0644 "$LIB/models.json.next"
  mv -f "$LIB/models.json.next" "$LIB/models.json"
fi
# Add Qwen on older installs and backfill defaults for the existing Q6 model.
mag_render "$SOURCE/ops/models/models.json" |
  jq --slurpfile installed "$LIB/models.json" '
    .bots.qwen as $qwen | $installed[0] |
    if (.bots | has("qwen") | not) then .bots.qwen = $qwen
    elif (.bots.qwen | .gpu == "mi50" and .model.local_dir == $qwen.model.local_dir)
    then .bots.qwen |= (
      .mmproj //= $qwen.mmproj |
      .mtp_args //= $qwen.mtp_args |
      .args = (reduce range(0; ($qwen.args | length); 2) as $i (.args // [];
        if any(.[]; split("=")[0] == $qwen.args[$i] or split("=")[0] == (
          {"--flash-attn":"-fa","-ctk":"--cache-type-k","-ctv":"--cache-type-v"}[$qwen.args[$i]]
        )) then . else . + $qwen.args[$i:$i+2] end))
    )
    end | select(. != $installed[0])
  ' > "$LIB/models.json.next"
if [[ -s $LIB/models.json.next ]]; then
  chmod 0644 "$LIB/models.json.next"
  mv -f "$LIB/models.json.next" "$LIB/models.json"
else
  rm -f "$LIB/models.json.next"
fi
install -m 0755 "$BIN" "$LIB/mag-modeld.next"
mv -f "$LIB/mag-modeld.next" "$LIB/mag-modeld"
for unit in mag-modeld.service mag-model@.service mag-model-fetch@.service mag-model-hip-build.service; do
  mag_render "$SOURCE/ops/models/$unit" > "/etc/systemd/system/$unit.next"
  chmod 0644 "/etc/systemd/system/$unit.next"
  mv -f "/etc/systemd/system/$unit.next" "/etc/systemd/system/$unit"
done
install -D -m 0644 "$SOURCE/ops/models/mag-model-qwen.conf" /etc/systemd/system/mag-model@qwen.service.d/20-mi50.conf
systemctl daemon-reload
systemctl enable mag-modeld.service >/dev/null
# The supervisor is stateless: restarting it never touches a running model.
systemctl restart mag-modeld.service
if [[ -x $MAG_IDOL_BASE/rocm-6.3.4/current/lib/llvm/bin/clang++ ]]; then
  systemctl start --no-block mag-model-hip-build.service
fi
echo 'Model layer installed: mag-modeld supervises Blake (:8191), Noah (:8192) and Qwen (:8193)'
