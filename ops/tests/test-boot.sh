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
esac
[[ $1 == is-enabled ]] && { echo enabled; exit 0; }
exit 0
S
cat > "$tmp/bin/virsh" <<'V'
#!/usr/bin/env bash
echo "virsh $*" >> "$CALLS"
case "$*" in "-q list --name") printf 'magbot-agent1\nother-vm\n' ;; "-q net-list --name") echo mag-agents ;; esac
V
chmod +x "$tmp/bin/"*
out=$(CALLS="$log" ACTIVE="mag-llm.service mag-llm-iq3.service mag-auto-update.timer mag-120.service" \
  PATH="$tmp/bin:$PATH" bash <(sed 's/^\[\[ \$EUID == 0 \]\].*$//' "$root/ops/boot/ensure-boot.sh"))
grep -Fxq 'systemctl add-wants multi-user.target mag-llm-iq3.service' "$log"
grep -Fxq 'systemctl enable mag-auto-update.timer' "$log"
! grep -Eq 'systemctl enable mag-llm.service|mag-console-only' "$log"
! grep -Eq 'systemctl (start|stop|restart|reload|try-restart|kill|daemon-reload)|--now' "$log"
grep -Fxq 'virsh -q autostart magbot-agent1' "$log"
! grep -Fq 'autostart other-vm' "$log"
grep -Fq 'BIOS' <<<"$out"
grep -Eq '^WantedBy=multi-user.target' "$root/ops/deepseek/mag-llm-iq3.service"
grep -Fq 'ops/boot/ensure-boot.sh' "$root/ops/auto-update/apply.sh"
grep -Fq 'PHASE boot' <<<"$(bash "$root/ops/update.sh" --dry-run)"
echo 'Boot persistence PASS'
