#!/usr/bin/env bash
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
run() { # $1 = secondary-model.env path, $2 = space-separated ipmi devices
  : > "$log"
  CALLS="$log" ACTIVE="mag-llm.service mag-llm-iq3.service mag-auto-update.timer mag-120.service $ORNITH" \
    MAG_SECONDARY_MODEL_ENV="$1" MAG_AGENTS_JSON="$tmp/none.json" MAG_IPMI_DEVICES="$2" \
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
grep -Fq 'retire-deepseek.sh' "$root/ops/deploy.sh"
grep -Fq 'ops/boot/ensure-boot.sh' "$root/ops/auto-update/apply.sh"
grep -Fq 'PHASE boot' <<<"$(bash "$root/ops/update.sh" --dry-run)"
echo 'Boot persistence PASS'
