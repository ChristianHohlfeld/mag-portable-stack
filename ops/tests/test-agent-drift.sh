#!/usr/bin/env bash
# Fixture only. Healthy room bots stay; unreferenced room-named (agent<N>,
# bot<N>) domains, directories and units are handed to magbotctl delete.
# agents.json is not modified.
set -euo pipefail
root=$(cd -- "$(dirname "$0")/../.." && pwd)
t=$(mktemp -d); trap 'rm -rf "$t"' EXIT
mkdir -p "$t/room" "$t/bin" "$t/units"
printf '%s\n' '[{"id":"q4","name":"Q4","vm":"agent1","memory":[]},{"id":"iq","name":"IQ3","vm":"agent3","pending_vm":"agent4","unavailable_vm":"agent8","memory":[]},{"id":"n","vm":"bot1"}]' > "$t/room/agents.json"
before=$(cat "$t/room/agents.json")
printf 'magbot-agent1\nmagbot-agent2\nmagbot-agent3\nmagbot-bot1\nmagbot-bot2\nmagbot-manual\nnot-a-bot\n' > "$t/domains"
printf '[Service]\nExecStart=/bin/false\n' > "$t/units/magbot-agent9.service"
printf '[Service]\nExecStart=/bin/true\n' > "$t/units/magbot-agent1.service"
printf '[Service]\nExecStart=/bin/true\n' > "$t/units/mag-llm.service"
cat > "$t/bin/magbotctl" <<'EOF'
#!/bin/sh
printf '%s\n' "$*" >> "$LOG"
rm -f "$MAG_DRIFT_UNIT_DIR/magbot-$2.service"
exit 0
EOF
chmod +x "$t/bin/magbotctl"
log=$t/log
env MAG_ROOM_STATE="$t/room" MAG_DRIFT_DOMAINS_FILE="$t/domains" MAG_DRIFT_DIRS="agent5 agent1 agent8" \
  MAG_DRIFT_UNIT_DIR="$t/units" MAG_DRIFT_MAGBOTCTL="$t/bin/magbotctl" LOG="$log" \
  bash "$root/ops/agent-drift.sh" > "$t/out"
for k in agent1 agent3 agent8 bot1; do grep -Fxq "keep $k" "$t/out"; done
for r in agent2 agent5 agent9 bot2; do
  grep -Fxq "remove unreferenced $r" "$t/out"
  grep -Fxq "delete $r" "$log"
done
# agent4 is only a pending_vm; non-room names (manual CLI bots) are never drift targets.
! grep -Eq 'delete (agent1|agent3|agent4|agent8|bot1|manual)$' "$log"
test ! -e "$t/units/magbot-agent9.service"
test -e "$t/units/magbot-agent1.service"
test -e "$t/units/mag-llm.service"
[[ "$(cat "$t/room/agents.json")" == "$before" ]]
# Unreadable room data removes nothing.
printf '{' > "$t/room/agents.json"
if env MAG_ROOM_STATE="$t/room" MAG_DRIFT_DOMAINS_FILE="$t/domains" MAG_DRIFT_MAGBOTCTL="$t/bin/magbotctl" \
  LOG="$t/log2" bash "$root/ops/agent-drift.sh" >"$t/out2" 2>"$t/err"; then
  echo 'unreadable agents.json was accepted' >&2; exit 1
fi
[[ ! -s "$t/log2" ]] || { echo 'delete ran without a readable room' >&2; exit 1; }
echo 'agent-drift PASS'
