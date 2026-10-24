#!/usr/bin/env bash
# ops/models/retire-deepseek.sh disables DeepSeek only on an Ornith host with both
# node servers healthy; otherwise it skips with WARN. Never mag-120, never rm models.
set -euo pipefail
root=$(cd -- "$(dirname "$0")/../.." && pwd)
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/bin" "$tmp/wants"; log="$tmp/calls"
cat > "$tmp/bin/systemctl" <<'S'
#!/usr/bin/env bash
echo "systemctl $*" >> "$CALLS"
case "$1" in
  show) echo loaded ;;
  is-active) [[ " $ACTIVE " == *" $2 "* ]] && echo active || echo inactive ;;
  is-enabled) [[ " $ACTIVE " == *" $2 "* ]] && echo enabled || echo disabled ;;
esac
exit 0
S
cat > "$tmp/bin/curl" <<'C'
#!/usr/bin/env bash
for a; do case "$a" in *:8191/health) [[ $H0 == 1 ]]; exit ;; *:8192/health) [[ $H1 == 1 ]]; exit ;; esac; done
exit 7
C
chmod +x "$tmp/bin/"*
echo 'MAG_SECONDARY_MODEL=ornith' > "$tmp/ornith.env"
echo 'MAG_SECONDARY_MODEL=qwen' > "$tmp/qwen.env"
run() { # $1 env file, $2 H0, $3 H1, $4 active units
  : > "$log"
  CALLS="$log" ACTIVE="$4" H0="$2" H1="$3" MAG_RETIRE_TEST=1 MAG_SECONDARY_MODEL_ENV="$1" \
    MAG_WANTS_DIR="$tmp/wants" PATH="$tmp/bin:$PATH" bash "$root/ops/models/retire-deepseek.sh"
}
live="mag-llm.service mag-model-gate.timer mag-llm-iq3.service"
never() { if grep -Eq 'mag-120|restart|reload|rm ' "$log"; then echo 'touched room/restart' >&2; exit 1; fi; }
# 1) Ornith healthy: all DeepSeek units disabled --now, boot link removed.
ln -s /dev/null "$tmp/wants/mag-llm-iq3.service"
out=$(run "$tmp/ornith.env" 1 1 "$live")
for u in mag-llm.service mag-model-gate.timer mag-llm-iq3.service; do
  grep -Fxq "systemctl disable --now $u" "$log" || { echo "$u not retired" >&2; exit 1; }
done
[[ ! -e $tmp/wants/mag-llm-iq3.service && ! -L $tmp/wants/mag-llm-iq3.service ]]
never
# 2) Idempotent: nothing active/enabled any more -> no disable call.
out=$(run "$tmp/ornith.env" 1 1 "")
if grep -q 'disable' "$log"; then echo 'not idempotent' >&2; exit 1; fi
# 3) Ornith node 1 unhealthy: skip with WARN, DeepSeek stays.
out=$(run "$tmp/ornith.env" 1 0 "$live")
grep -Fq 'WARN' <<<"$out"; if grep -q 'disable' "$log"; then exit 1; fi
# 4) Not an Ornith host (Qwen/legacy): skip.
out=$(run "$tmp/qwen.env" 1 1 "$live")
grep -Fq 'WARN' <<<"$out"; if grep -q 'disable' "$log"; then exit 1; fi
out=$(run "$tmp/missing.env" 1 1 "$live")
grep -Fq 'WARN' <<<"$out"; if grep -q 'disable' "$log"; then exit 1; fi
# Wiring: deploy retires after commit (trap cleared) and before ensure-boot; never deletes models.
d="$root/ops/deploy.sh"
r=$(grep -n 'retire-deepseek.sh' "$d" | head -1 | cut -d: -f1)
t=$(grep -n '^trap - EXIT INT TERM' "$d" | cut -d: -f1)
b=$(grep -n 'ops/boot/ensure-boot.sh"' "$d" | cut -d: -f1)
(( t < r && r < b )) || { echo 'retire not between commit and ensure-boot' >&2; exit 1; }
if grep -v '^#' "$root/ops/models/retire-deepseek.sh" | grep -Eq 'rm -[a-z]*r|/models/'; then echo 'retire touches model files' >&2; exit 1; fi
if grep -Eq 'systemctl (enable|start|restart)[^|]*mag-llm(\.service|-iq3)|systemctl (enable|start|restart)[^|]*mag-model-(gate|pin)' \
   "$d" "$root/ops/boot/ensure-boot.sh" "$root/ops/bootstrap.sh" "$root/ops/update.sh" "$root/ops/auto-update/"*.sh "$root/ops/health.sh"; then
  echo 'a script still enables/starts DeepSeek' >&2; exit 1
fi
grep -Fq '20-room-audit.conf' "$d"
if grep -E '^After=' "$root/ops/mag-120.service" | grep -q 'mag-llm'; then echo 'mag-120 still After=mag-llm' >&2; exit 1; fi
echo 'Retire DeepSeek PASS'
