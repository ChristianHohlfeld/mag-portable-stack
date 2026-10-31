#!/usr/bin/env bash
# mag-test-timeout: 180
if [ -z "${MAG_TEST_DEADLINE_ARMED:-}" ]; then
  export MAG_TEST_DEADLINE_ARMED=1
  exec timeout --kill-after=5s 180s bash "$0" "$@"
fi
# ensure-boot.sh enables what is running for the next boot and never starts,
# stops, restarts or reloads anything.
set -euo pipefail
root=$(cd -- "$(dirname "$0")/../.." && pwd)
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
mkdir "$tmp/bin"; log="$tmp/calls"
cat > "$tmp/bin/systemctl" <<'S'
#!/usr/bin/env bash
echo "systemctl $*" >> "$CALLS"
case "$1 $2" in
  "is-active --quiet") [[ " $ACTIVE " == *" $3 "* ]]; exit ;;
  "is-enabled mag-llm.service") echo enabled; exit 0 ;;
  "is-enabled mag-llm-iq3.service") echo static; exit 0 ;;
  "is-enabled mag-auto-update.timer") echo disabled; exit 1 ;;
  "is-enabled mag-llm-glm.service") echo disabled; exit 1 ;;
  "is-enabled mag-llm-qwen-room.service") echo disabled; exit 1 ;;
  "is-enabled mag-llm-glm@q2.service") echo disabled; exit 1 ;;
  "is-enabled mag-console-only.service") echo disabled; exit 1 ;;
  "is-enabled mag-llm-ornith"*) echo disabled; exit 1 ;;
esac
[[ $1 == is-enabled ]] && { echo enabled; exit 0; }
exit 0
S
cat > "$tmp/bin/virsh" <<'V'
#!/usr/bin/env bash
echo "virsh $*" >> "$CALLS"
case "$*" in "-q list --name") printf 'magbot-agent1\nother-vm\n' ;; "-q net-list --name") echo mag-agents ;; esac
V
# Host-independent: on MAG /dev/ipmi0 exists and the real ipmitool needs root.
cat > "$tmp/bin/ipmitool" <<'I'
#!/usr/bin/env bash
echo "ipmitool $*" >> "$CALLS"
I
chmod +x "$tmp/bin/"*
# Host-independent: never read the host's secondary-model.env, agents.json or /dev/ipmi*.
run() { # $1 = secondary-model.env path, $2 = ipmi devices, $3 = room-model.env (missing if omitted)
  local room=${3:-$tmp/no-room-model.env}
  : > "$log"
  CALLS="$log" ACTIVE="${MAG_TEST_ACTIVE:-mag-llm.service mag-llm-iq3.service mag-auto-update.timer mag-120.service $ORNITH}" \
    MAG_SECONDARY_MODEL_ENV="$1" MAG_ROOM_MODEL_ENV="$room" MAG_AGENTS_JSON="$tmp/none.json" MAG_IPMI_DEVICES="$2" \
    PATH="$tmp/bin:$PATH" bash <(sed 's/^\[\[ \$EUID == 0 \]\].*$//' "$root/ops/boot/ensure-boot.sh")
}
common() {
  grep -Fxq 'systemctl enable mag-auto-update.timer' "$log"
  # DeepSeek is retired: an active mag-llm/IQ3 is never persisted for boot.
  if grep -Eq 'systemctl (enable|add-wants multi-user.target) mag-llm(-iq3)?\.service|mag-console-only' "$log"; then exit 1; fi
  if grep -Eq 'systemctl (start|stop|restart|reload|try-restart|kill|daemon-reload)|--now' "$log"; then exit 1; fi
  grep -Fxq 'virsh -q autostart magbot-agent1' "$log"
  if grep -Fq 'autostart other-vm' "$log"; then exit 1; fi
}
ornith_units="mag-llm-ornith@1.service mag-llm-ornith@2.service mag-llm-ornith@3.service mag-llm-ornith@4.service"

# 1) No secondary-model.env, no BMC: legacy host; mag-llm/IQ3 run but are not persisted.
out=$(ORNITH="" run "$tmp/missing.env" "$tmp/no-ipmi0")
common
grep -Fq 'BIOS' <<<"$out"
if grep -q '^ipmitool' "$log"; then exit 1; fi

# 2) Ornith x4 host with a BMC: 4 ornith units for boot, never Qwen/IQ3.
echo 'MAG_SECONDARY_MODEL=ornith' > "$tmp/ornith.env"; touch "$tmp/ipmi0"
out=$(ORNITH="$ornith_units" run "$tmp/ornith.env" "$tmp/ipmi0")
for u in $ornith_units; do grep -Fxq "systemctl enable $u" "$log" || exit 1; done
if grep -Eq 'systemctl (enable|add-wants multi-user.target) mag-llm-(iq3|qwen)' "$log"; then exit 1; fi
grep -Fxq 'systemctl disable mag-llm-iq3.service mag-llm-qwen.service' "$log"
common
grep -Fq 'Ornith host' <<<"$out"
grep -Fq 'BMC power policy' <<<"$out"
grep -Fxq 'ipmitool chassis policy always-on' "$log"

# 3) Per-node Ornith servers (variant B) active: both enabled, never the disabled legacy instances.
out=$(ORNITH="mag-llm-ornith-node@0.service mag-llm-ornith-node@1.service" run "$tmp/ornith.env" "$tmp/ipmi0")
grep -Fxq 'systemctl enable mag-llm-ornith-node@0.service' "$log"
grep -Fxq 'systemctl enable mag-llm-ornith-node@1.service' "$log"
if grep -Eq 'systemctl enable mag-llm-ornith@' "$log"; then exit 1; fi
common

# 4) A room-model.env is the active model, even when secondary-model.env says
#    Ornith and even when the host file /etc/mag-stack/room-model.env exists.
printf 'MAG_ROOM_MODEL_ID_0=deepseek\n' > "$tmp/room.env"
echo 'MAG_SECONDARY_MODEL=ornith' > "$tmp/ornith-too.env"
out=$(ORNITH="mag-llm-ds-node@0.service mag-llm-ds-node@1.service" run "$tmp/ornith-too.env" "$tmp/ipmi0" "$tmp/room.env")
grep -Fq 'systemctl disable mag-llm-ornith@1.service mag-llm-ornith@2.service mag-llm-ornith@3.service mag-llm-ornith@4.service mag-llm-ornith-node@0.service mag-llm-ornith-node@1.service' "$log"
if grep -Eq 'systemctl enable mag-llm-ornith' "$log"; then exit 1; fi
if grep -Fq 'Ornith host' <<<"$out"; then exit 1; fi
common
grep -Fq 'retire-deepseek.sh' "$root/ops/deploy.sh"
grep -Fq 'clear-failed-units.sh' "$root/ops/deploy.sh"
grep -Fq 'ops/boot/ensure-boot.sh' "$root/ops/auto-update/apply.sh"
# 5) GLM room persists Qwen NUMA0 + GLM-Q2 NUMA1; Q1/legacy units stay disabled.
printf 'MAG_ROOM_MODEL_ID_0=qwen3.8-flash-next-uncensored\nMAG_ROOM_MODEL_ID_1=glm-5.3-flash\n' > "$tmp/room-glm.env"
out=$(ORNITH="" MAG_TEST_ACTIVE="mag-llm-qwen-room.service mag-llm-glm@q2.service mag-auto-update.timer mag-120.service" run "$tmp/missing.env" "$tmp/no-ipmi0" "$tmp/room-glm.env")
grep -Fxq 'systemctl disable mag-llm-ds-node@0.service mag-llm-ds-node@1.service mag-llm-glm.service mag-llm-glm@q1.service mag-llm-qwen.service' "$log"
grep -Fxq 'systemctl enable mag-llm-qwen-room.service' "$log"
grep -Fxq 'systemctl enable mag-llm-glm@q2.service' "$log"
! grep -Eq 'systemctl enable mag-llm-(qwen|glm@q1|ds-node@[01]).service' "$log"
common
grep -Fq 'PHASE boot' <<<"$(bash "$root/ops/update.sh" --dry-run)"
echo 'Boot persistence PASS'
