#!/usr/bin/env bash
# Remove half-created bot VMs: a libvirt domain, disk directory or unit with
# no matching vm/pending_vm/unavailable_vm in the room. Healthy bots and in-progress spawns
# stay. agents.json is never modified. mag-llm and mag-llm-iq3 are not touched.
set -uo pipefail
ROOM=${MAG_ROOM_STATE:-/var/lib/magbot/room}
AGENTS=$ROOM/agents.json
ROOT=${MAGBOT_ROOT:-/var/lib/libvirt/images/magbot}
MAGBOTCTL=${MAG_DRIFT_MAGBOTCTL:-/usr/local/bin/magbotctl}
VIRSH=${MAG_DRIFT_VIRSH:-virsh}
UNIT_DIR=${MAG_DRIFT_UNIT_DIR:-/etc/systemd/system}
[[ -f "$AGENTS" ]] || { echo 'agent drift refused: agents.json missing; nothing removed' >&2; exit 1; }

keep_file=$(mktemp)
if ! python3 -I - "$AGENTS" >"$keep_file" <<'PY'
import json, re, sys
ids = set()
try:
    data = json.load(open(sys.argv[1]))
except Exception as exc:
    sys.stderr.write('agent drift refused: agents.json unreadable (%s)\n' % exc)
    sys.exit(2)
if not isinstance(data, list):
    sys.stderr.write('agent drift refused: agents.json is not a list\n')
    sys.exit(2)
for bot in data:
    if not isinstance(bot, dict):
        continue
    for key in ('vm', 'pending_vm', 'unavailable_vm'):
        value = bot.get(key) or ''
        if isinstance(value, str) and re.fullmatch(r'(agent|bot)[0-9]+', value):
            ids.add(value)
for value in sorted(ids):
    print(value)
PY
then
  rm -f -- "$keep_file"
  echo 'agent drift refused: could not read room entries; nothing removed' >&2
  exit 1
fi
mapfile -t keep < "$keep_file"
rm -f -- "$keep_file"

declare -A kept=()
if ((${#keep[@]})); then
  for id in "${keep[@]}"; do kept[$id]=1; done
fi

declare -A seen=()
if [[ -n "${MAG_DRIFT_DOMAINS_FILE:-}" ]]; then
  mapfile -t domains < "$MAG_DRIFT_DOMAINS_FILE"
else
  mapfile -t domains < <("$VIRSH" -c qemu:///system list --all --name 2>/dev/null || true)
fi
for name in "${domains[@]+"${domains[@]}"}"; do
  [[ "$name" == magbot-* ]] || continue
  id=${name#magbot-}
  [[ "$id" =~ ^(agent|bot)[0-9]+$ ]] || continue
  seen[$id]=1
done
if [[ -n "${MAG_DRIFT_DIRS:-}" ]]; then
  for id in $MAG_DRIFT_DIRS; do
    [[ "$id" =~ ^(agent|bot)[0-9]+$ ]] && seen[$id]=1
  done
else
  for dir in "$ROOT"/magbot-*; do
    [[ -d "$dir" && ! -L "$dir" ]] || continue
    id=${dir##*/magbot-}
    [[ "$id" =~ ^(agent|bot)[0-9]+$ ]] && seen[$id]=1
  done
fi
for unit in "$UNIT_DIR"/magbot-*.service; do
  [[ -f "$unit" && ! -L "$unit" ]] || continue
  id=$(basename "$unit" .service)
  id=${id#magbot-}
  [[ "$id" =~ ^(agent|bot)[0-9]+$ ]] && seen[$id]=1
done

fail=0
ids=()
if ((${#seen[@]})); then
  for id in "${!seen[@]}"; do ids+=("$id"); done
fi
for id in "${ids[@]+"${ids[@]}"}"; do
  if [[ -n "${kept[$id]:-}" ]]; then
    echo "keep $id"
    continue
  fi
  echo "remove unreferenced $id"
  if [[ "${MAG_DRIFT_DRY_RUN:-0}" == 1 ]]; then continue; fi
  # Re-read the room right before deleting: a spawn may have claimed the
  # name since the snapshot. magbotctl delete takes the per-bot lock, so a
  # running create job is never deleted underneath.
  if grep -Eq "\"(vm|pending_vm|unavailable_vm)\": *\"$id\"" "$AGENTS"; then
    echo "keep newly referenced $id"
  elif ! "$MAGBOTCTL" delete "$id"; then
    echo "delete $id failed" >&2
    fail=1
  fi
done
exit "$fail"
