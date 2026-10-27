#!/usr/bin/env bash
# mag-test-timeout: 180
if [ -z "${MAG_TEST_DEADLINE_ARMED:-}" ]; then
  export MAG_TEST_DEADLINE_ARMED=1
  exec timeout --kill-after=5s 180s bash "$0" "$@"
fi
# Fixture only. Healthy room bots stay; unreferenced domains, directories and
# units are handed to despawn. agents.json is not modified.
set -euo pipefail
root=$(cd -- "$(dirname "$0")/../.." && pwd)
t=$(mktemp -d); trap 'rm -rf "$t"' EXIT
mkdir -p "$t/room" "$t/bin" "$t/units"
printf '%s\n' '[{"id":"q4","name":"Q4","vm":"agent1","memory":[]},{"id":"iq","name":"IQ3","vm":"agent3","pending_vm":"agent4","unavailable_vm":"agent8","memory":[]}]' > "$t/room/agents.json"
before=$(cat "$t/room/agents.json")
printf 'magbot-agent1\nmagbot-agent2\nmagbot-agent3\nnot-a-bot\n' > "$t/domains"
printf '[Service]\nExecStart=/bin/false\n' > "$t/units/magbot-agent9.service"
printf '[Service]\nExecStart=/bin/true\n' > "$t/units/magbot-agent1.service"
printf '[Service]\nExecStart=/bin/true\n' > "$t/units/mag-llm.service"
cat > "$t/bin/magbot" <<'EOF'
#!/bin/sh
printf '%s\n' "$*" >> "$LOG"
unit="$MAG_DRIFT_UNIT_DIR/magbot-$2.service"
if [ -f "$unit" ]; then
 systemctl disable --now "magbot-$2.service"
 rm -f "$unit"
 systemctl daemon-reload
fi
exit 0
EOF
cat > "$t/bin/systemctl" <<'EOF'
#!/bin/sh
printf '%s\n' "$*" >> "$LOG"
exit 0
EOF
chmod +x "$t/bin/magbot" "$t/bin/systemctl"
log=$t/log
env MAG_ROOM_STATE="$t/room" MAG_DRIFT_DOMAINS_FILE="$t/domains" MAG_DRIFT_DIRS="agent5 agent1 agent8" \
  MAG_DRIFT_UNIT_DIR="$t/units" MAG_DRIFT_MAGBOT="$t/bin/magbot" LOG="$log" PATH="$t/bin:$PATH" \
  bash "$root/ops/agent-drift.sh" >"$t/out"
grep -Fxq 'keep agent1' "$t/out"
grep -Fxq 'keep agent3' "$t/out"
grep -Fxq 'keep agent8' "$t/out"
# agent4 is only a pending_vm. It has no domain, so it is not listed, and it is not removed.
grep -Fxq 'remove unreferenced agent2' "$t/out"
grep -Fxq 'remove unreferenced agent5' "$t/out"
grep -Fxq 'remove unreferenced agent9' "$t/out"
grep -Fxq 'despawn-orphan agent2' "$log"
grep -Fxq 'despawn-orphan agent5' "$log"
grep -Fxq 'despawn-orphan agent9' "$log"
! grep -Eq 'despawn-orphan agent1|despawn-orphan agent3|despawn-orphan agent4|despawn-orphan agent8' "$log"
grep -Fxq 'disable --now magbot-agent9.service' "$log"
grep -Fxq 'daemon-reload' "$log"
test ! -e "$t/units/magbot-agent9.service"
test -e "$t/units/magbot-agent1.service"
test -e "$t/units/mag-llm.service"
! grep -Eq 'mag-llm|restart|stop' "$log"
[[ "$(cat "$t/room/agents.json")" == "$before" ]]
# Unreadable room data removes nothing.
printf '{' > "$t/room/agents.json"
if env MAG_ROOM_STATE="$t/room" MAG_DRIFT_DOMAINS_FILE="$t/domains" MAG_DRIFT_MAGBOT="$t/bin/magbot" \
  LOG="$t/log2" PATH="$t/bin:$PATH" bash "$root/ops/agent-drift.sh" >"$t/out2" 2>"$t/err"; then
  echo 'unreadable agents.json was accepted' >&2; exit 1
fi
if [[ -f "$t/log2" ]] && grep -Fq despawn "$t/log2"; then echo 'despawn ran without a readable room' >&2; exit 1; fi
# Exercise the real guard without libvirt or a real VM: referenced and
# malformed room state must return BEFORE teardown touches any resource.
if [[ -x "$root/build/magbot-native" ]] && sudo -n true 2>/dev/null; then
  printf '%s\n' '[{"id":"new","pending_vm":"agent77"}]' > "$t/room/agents.json"
  mkdir -p "$t/native/bots/agent77"
  printf keep > "$t/native/bots/agent77/sentinel"
  rc=0
  sudo -n env MAGBOT_ROOT="$t/native" MAG_ROOM_STATE="$t/room" \
    "$root/build/magbot-native" despawn-orphan agent77 >"$t/native.out" 2>&1 || rc=$?
  [[ $rc == 3 ]]
  [[ $(cat "$t/native/bots/agent77/sentinel") == keep ]]
  printf '{' > "$t/room/agents.json"
  if sudo -n env MAGBOT_ROOT="$t/native" MAG_ROOM_STATE="$t/room" \
    "$root/build/magbot-native" despawn-orphan agent77 >"$t/native.err" 2>&1; then
    echo 'native cleanup accepted corrupt room state' >&2; exit 1
  fi
  [[ $(cat "$t/native/bots/agent77/sentinel") == keep ]]
fi
echo 'agent-drift PASS' 
