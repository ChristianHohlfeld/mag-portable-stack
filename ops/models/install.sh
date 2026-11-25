#!/usr/bin/env bash
# Install the model layer (run as root, from deploy.sh or by hand):
#   mag-modeld            supervisor: download watch, verify, start
#   mag-model@<bot>       Dedicated llama.cpp server per bot
#   mag-model-fetch@<bot> resumable download
# Never waits for a model, never restarts a running one and never deletes a
# model file.
set -euo pipefail
[[ $EUID == 0 ]] || { echo 'run as root' >&2; exit 1; }
SOURCE="$(cd -- "$(dirname -- "$0")/../.." && pwd)"
if [[ ${1:-} != --units-installed ]]; then
  bash "$SOURCE/ops/models/install-units.sh"
fi
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
exec 9>"$LIB/.install.lock"
flock 9
install -d -m 0755 "$STATE"
install -d -m 0755 -o "$MAG_OWNER" -g "$MAG_OWNER" "$STATE/slots"
# The room (magbot) drops control verbs here; it cannot list or read others.
if getent group magbot >/dev/null; then
  install -d -m 0730 -o root -g magbot "$STATE/control"
else
  install -d -m 0700 "$STATE/control"
fi
pending="$LIB/.supervisor-restart"
install_if_changed() {
  local src=$1 dest=$2 mode=$3
  if cmp -s "$src" "$dest"; then return; fi
  install -m "$mode" "$src" "$dest.next"
  touch "$pending"
  mv -f "$dest.next" "$dest"
}
for f in run.sh run-mi50.sh gpu-guard.py mi50-mtp.py fetch.sh build-draft.sh; do
  install_if_changed "$SOURCE/ops/models/$f" "$LIB/$f" 0755
done
# Paths and the unit's user follow this host's owner (ops/host.env).
# A software deploy must not silently change local model selection.
# Replace the retired uncensored default once, or on explicit operator request.
replace=${MAG_REPLACE_MODEL_CONFIG:-0}
if [[ $replace != 0 && $replace != 1 ]]; then
  # Merge only the selected bot into the installed manifest.
  candidate="$LIB/models.json.merge"
  if ! mag_render "$SOURCE/ops/models/models.json" |
    jq -e --arg bot "$replace" --slurpfile installed "$LIB/models.json" '
      .bots[$bot] as $spec | $installed[0] |
      if $spec != null and (.bots | has($bot)) then .bots[$bot] = $spec
      else error("unknown model: " + $bot) end
    ' > "$candidate"; then
    rm -f "$candidate"
    exit 1
  fi
  install_if_changed "$candidate" "$LIB/models.json" 0644
  rm -f "$candidate"
elif [[ ! -f "$LIB/models.json" || $replace == 1 ]] || jq -e '.bots | to_entries | any((.value.model.path // "") | contains("/gemma4-26b-a4b-uncensored/"))' "$LIB/models.json" >/dev/null; then
  mag_render "$SOURCE/ops/models/models.json" > "$LIB/models.json.next"
  chmod 0644 "$LIB/models.json.next"
  if ! cmp -s "$LIB/models.json.next" "$LIB/models.json"; then
    touch "$pending"
    mv -f "$LIB/models.json.next" "$LIB/models.json"
  fi
  rm -f "$LIB/models.json.next"
fi
# Add Qwen on older installs and backfill defaults for the existing Q6 model.
if [[ $replace == 0 || $replace == 1 ]]; then
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
    touch "$pending"
    mv -f "$LIB/models.json.next" "$LIB/models.json"
  else
    rm -f "$LIB/models.json.next"
  fi
fi
install_if_changed "$BIN" "$LIB/mag-modeld" 0755
systemctl enable mag-modeld.service >/dev/null
# Only the supervisor needs to reread changed launch inputs. Model instances
# remain under its management with the same process and slot.
if [[ -e $pending ]]; then
  systemctl restart mag-modeld.service
  rm -f "$pending"
else
  systemctl start mag-modeld.service
fi
if [[ $replace == 0 || $replace == 1 ]]; then
  systemctl start --no-block mag-model-cpu-build.service
  if [[ -x $MAG_IDOL_BASE/rocm-6.3.4/current/lib/llvm/bin/clang++ ]]; then
    systemctl start --no-block mag-model-hip-build.service
  fi
fi
echo 'Model layer installed: mag-modeld supervises Blake (:8191), Noah (:8192) and Qwen (:8193)'
