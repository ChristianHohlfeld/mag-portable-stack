#!/usr/bin/env bash
# Transactional operator-only NUMA1 IQ3 -> Qwen. Q4 on NUMA0 is never restarted.
set -Eeuo pipefail
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
ACTION=${1:-check}
SNAP=/var/lib/mag-qwen/cutover
CONF=/etc/mag-stack/secondary-model.env
GATE=/opt/mag-audit/bin/mag-audit-gateway
GUNIT=/etc/systemd/system/mag-audit-gateway.service
OLD=mag-llm-iq3.service
NEW=mag-llm-qwen.service
fail() { echo "Qwen cutover: $*" >&2; exit 4; }
[[ $EUID == 0 ]] || fail "root required"
[[ $ACTION == check || $ACTION == apply || $ACTION == resume || $ACTION == rollback || $ACTION == status ]] || fail "usage: cutover.sh check|apply|resume|rollback|status"
exec 8>/run/lock/mag-private-deploy.lock
flock -n 8 || fail "MAG deploy/cutover already in progress"
# shellcheck source=../deploy-guard.sh
source "$ROOT/ops/deploy-guard.sh"
mag_cloexec 8
health() { curl --noproxy '*' -fsS -o /dev/null --max-time 5 "http://127.0.0.1:$1/health"; }
pid() { systemctl show -p MainPID --value "$1" 2>/dev/null || true; }
wait_health() {
  local port=$1 count=$2 i
  for ((i=0;i<count;i++)); do health "$port" && return 0; sleep 10; done
  return 1
}
room_idle() {
  local state
  state=$(curl --noproxy '*' -fsS --max-time 5 http://127.0.0.1:8766/api/state) || fail "room unavailable"
  python3 -I -c '
import json,sys
s=json.load(sys.stdin)
assert not any(s.get(k) for k in ("busy","pending","queue","busy_bots","waiting_bots","jobruns")),"room busy/queued"
' <<<"$state" || fail "MAG has active or queued work"
}
backups_idle() {
  local u
  for u in mag-backup-daily mag-backup-weekly mag-backup-monthly mag-backup-restore-test mag-backup-deploy mag-backup-clear; do
    systemctl is-active --quiet "$u.service" && fail "backup running: $u"
  done
  return 0
}
q4_untouched() {
  [[ $(pid mag-llm.service) == "$Q4_PID" ]] || fail "Q4 PID changed"
  health 8090 || fail "Q4 unhealthy"
}
restore() {
  [[ -f "$SNAP/READY" ]] || fail "rollback snapshot unavailable"
  echo "Qwen: restore previous gateway + IQ3; Q4 remains untouched" >&2
  systemctl stop "$NEW" || fail "Qwen stop failed; IQ3 must remain stopped"
  [[ $(pid "$NEW") == 0 ]] || fail "Qwen still has a live PID; IQ3 start forbidden"
  ! health 8091 || fail "NUMA1 port 8091 still serving; IQ3 start forbidden"
  systemctl disable "$NEW" || true
  if [[ $(cat "$SNAP/iq3-active") == yes ]]; then
    systemctl start "$OLD" || fail "IQ3 restore failed"
    wait_health 8091 1080 || fail "IQ3 failed to load"
  fi
  install -m 0755 "$SNAP/gateway.bin" "$GATE.next"
  mv -f "$GATE.next" "$GATE"
  install -m 0644 "$SNAP/gateway.unit" "$GUNIT"
  if [[ -f "$SNAP/no-conf" ]]; then rm -f "$CONF"; else install -m 0644 "$SNAP/secondary.env" "$CONF"; fi
  systemctl daemon-reload
  systemctl restart mag-audit-gateway.service
  systemctl restart mag-120.service
  if [[ $(cat "$SNAP/iq3-enabled") == yes ]]; then systemctl enable "$OLD"; else systemctl disable "$OLD"; fi
  q4_untouched
  echo "IQ3/gateway restored; Q4 unchanged"
}
if [[ $ACTION == status ]]; then
  for s in mag-llm.service "$OLD" "$NEW" mag-audit-gateway.service mag-120.service; do
    echo "$s: $(systemctl is-active "$s" 2>/dev/null || true) pid=$(pid "$s")"
  done
  [[ ! -f "$CONF" ]] || cat "$CONF"
  [[ ! -f "$SNAP/READY" ]] || echo "Rollback snapshot: $SNAP"
  exit 0
fi
Q4_PID=$(pid mag-llm.service)
[[ $Q4_PID =~ ^[1-9][0-9]*$ ]] || fail "Q4 not running"
q4_untouched
room_idle
backups_idle
# Resume interrupted first cutover when Qwen is already warm and snapshot READY.
# Do not restart Q4 or Qwen. The audit firewall remains enforced.
if [[ $ACTION == resume ]]; then
  [[ -f "$SNAP/READY" && -f "$SNAP/gateway.bin" && -f "$SNAP/gateway.unit" ]] || fail "original rollback snapshot incomplete"
  [[ -f "$SNAP/no-conf" || -f "$SNAP/secondary.env" ]] || fail "original config snapshot missing"
  [[ -f "$SNAP/iq3-active" && -f "$SNAP/iq3-enabled" ]] || fail "original IQ3 state missing"
  [[ -x "$ROOT/build/mag-audit-gateway" && -f "$ROOT/ops/audit/systemd/mag-audit-gateway.service" ]] || fail "Qwen-aware gateway build/unit missing"
  [[ -x "$GATE" && -f "$GUNIT" ]] || fail "installed gateway missing"
  if [[ -s "$CONF" ]]; then
    grep -Eq '^MAG_SECONDARY_MODEL=(iq3|qwen)$' "$CONF" || fail "unknown secondary-model configuration"
  fi
  systemctl is-active --quiet "$NEW" || fail "warm Qwen service not running"
  ! systemctl is-active --quiet "$OLD" || fail "IQ3 still active on NUMA1"
  [[ $(pid "$NEW") =~ ^[1-9][0-9]*$ ]] || fail "Qwen has no live PID"
  health 8091 || fail "Qwen backend not ready on 8091"
  q4_untouched
  echo "Resuming Qwen gateway/room routing, retaining warmed Q4 and Qwen ..."
  # Only routing is reverted on failure. Never stop the warmed models.
  restore_route() {
    local rc=$?
    trap - EXIT INT TERM
    echo "Qwen resume failed ($rc); reverting gateway/room routing" >&2
    install -m 0755 "$SNAP/gateway.bin" "$GATE.next" && mv -f "$GATE.next" "$GATE" || true
    install -m 0644 "$SNAP/gateway.unit" "$GUNIT" || true
    if [[ -f "$SNAP/no-conf" ]]; then rm -f "$CONF"; else install -m 0644 "$SNAP/secondary.env" "$CONF"; fi
    systemctl daemon-reload || true
    systemctl restart mag-audit-gateway.service || true
    systemctl restart mag-120.service || true
    echo "Models untouched; rollback snapshot retained: $SNAP" >&2
    exit "$rc"
  }
  trap restore_route EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM
  install -m 0755 "$ROOT/build/mag-audit-gateway" "$GATE.next"
  mv -f "$GATE.next" "$GATE"
  install -m 0644 "$ROOT/ops/audit/systemd/mag-audit-gateway.service" "$GUNIT"
  install -d -m 0755 /etc/mag-stack
  printf 'MAG_SECONDARY_MODEL=qwen\n' > "$CONF.next"
  chmod 0644 "$CONF.next"
  mv -f "$CONF.next" "$CONF"
  systemctl daemon-reload
  systemctl restart mag-audit-gateway.service
  systemctl restart mag-120.service
  wait_health 8080 30 || fail "Qwen-aware gateway did not become healthy"
  local_ready=0
  for ((i=0; i<30; i++)); do
    if curl --noproxy '*' -fsS --max-time 5 http://127.0.0.1:8766/api/state | python3 -I -c '
import json,sys
s=json.load(sys.stdin)
assert any(x.get("id")=="qwen3.8-flash-next-uncensored" for x in s.get("models",[])), "Qwen missing"
' 2>/dev/null; then
      local_ready=1
      break
    fi
    sleep 2
  done
  [[ $local_ready == 1 ]] || fail "MAG room did not advertise Qwen"
  q4_untouched
  health 8091 || fail "Qwen no longer healthy"
  systemctl enable "$NEW"
  systemctl disable "$OLD"
  trap - EXIT INT TERM
  echo "SUCCESS: Q4 unchanged; Qwen NUMA1 warm; MAG gateway/room list Qwen."
  echo "Rollback snapshot retained: $SNAP"
  exit 0
fi

if [[ $ACTION == rollback ]]; then
  restore
  rm -rf -- "$SNAP"
  exit 0
fi
[[ ! -e "$SNAP" ]] || fail "previous cutover snapshot exists; inspect/rollback before retrying"
[[ $(cat /sys/devices/system/node/node1/cpulist) == '14-27,42-55' ]] || fail "NUMA1 topology unexpected"
[[ -x "$ROOT/build/mag-audit-gateway" ]] || fail "build native gateway first"
[[ -x "$GATE" && -f "$GUNIT" ]] || fail "old gateway not installed"
[[ -x /usr/local/lib/mag-qwen/start.sh && -x /usr/local/lib/mag-qwen/verify-model.sh ]] || fail "stage Qwen first"
[[ -f /opt/mag-stack/current/SOURCE_COMMIT ]] || fail "deployed revision missing"
[[ $(cat /opt/mag-stack/current/SOURCE_COMMIT) == "$(git -c safe.directory="$ROOT" -C "$ROOT" rev-parse HEAD)" ]] || fail "deploy current green first"
[[ ! -s "$CONF" ]] || grep -Fxq 'MAG_SECONDARY_MODEL=iq3' "$CONF" || fail "secondary config unexpected"
! systemctl is-active --quiet "$NEW" || fail "Qwen already active"
! systemctl is-enabled --quiet "$NEW" || fail "Qwen already enabled"
# Full pinned HF GGUF SHA: no half-downloaded file may be activated.
# Staging already checked the full 98 GB upstream SHA. The Qwen service
# rechecks that SHA at ExecStart; avoid redundant full-disk reads here.
MODEL=/home/chris/models/qwen3.8-flash-next-uncensored/Qwen3.8-Flash-Next-Uncensored.IQ4_XS.gguf
[[ -f "$MODEL" && $(stat -c %s "$MODEL") -ge 90000000000 ]] || fail "Qwen download incomplete"
[[ -s /etc/mag-qwen/model.sha256 ]] || fail "upstream digest missing"
q4_untouched
if [[ $ACTION == check ]]; then echo "PASS: NUMA1 cutover ready, Q4 untouched"; exit 0; fi
install -d -m 0700 "$SNAP"
install -m 0755 "$GATE" "$SNAP/gateway.bin"
install -m 0644 "$GUNIT" "$SNAP/gateway.unit"
if [[ -f "$CONF" ]]; then install -m 0644 "$CONF" "$SNAP/secondary.env"; else touch "$SNAP/no-conf"; fi
if systemctl is-active --quiet "$OLD"; then echo yes > "$SNAP/iq3-active"; else echo no > "$SNAP/iq3-active"; fi
if systemctl is-enabled --quiet "$OLD"; then echo yes > "$SNAP/iq3-enabled"; else echo no > "$SNAP/iq3-enabled"; fi
touch "$SNAP/READY"
done_ok=0
cleanup() {
  local rc=$?
  trap - EXIT INT TERM
  if (( done_ok == 0 )); then
    echo "Qwen cutover failed ($rc). Restoring IQ3 and gateway..." >&2
    restore || echo "MANUAL recovery required; snapshot retained: $SNAP" >&2
  fi
  exit "$rc"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
# Never put two memory-heavy models on NUMA1 or port 8091.
if systemctl is-active --quiet "$OLD"; then systemctl stop "$OLD"; fi
! systemctl is-active --quiet "$OLD" || fail "IQ3 still active"
[[ $(pid "$OLD") == 0 ]] || fail "IQ3 still has a live PID; Qwen start forbidden"
! health 8091 || fail "port 8091 occupied after IQ3 stop"
q4_untouched
backups_idle
systemctl start "$NEW"
wait_health 8091 1080 || fail "Qwen failed to load within three hours"
q4_untouched
python3 -I "$ROOT/ops/qwen/smoke.py" || fail "direct Qwen chat/thinking/tools/stream failed"
# Model proven. Only now update the gateway/room, while no jobs are active.
room_idle
backups_idle
q4_untouched
install -m 0755 "$ROOT/build/mag-audit-gateway" "$GATE.next"
mv -f "$GATE.next" "$GATE"
install -m 0644 "$ROOT/ops/audit/systemd/mag-audit-gateway.service" "$GUNIT"
install -d -m 0755 /etc/mag-stack
printf 'MAG_SECONDARY_MODEL=qwen\n' > "$CONF.next"
chmod 0644 "$CONF.next"
mv -f "$CONF.next" "$CONF"
systemctl daemon-reload
systemctl restart mag-audit-gateway.service
systemctl restart mag-120.service
python3 -I "$ROOT/ops/qwen/smoke.py" --port 8080 --quick || fail "gateway Qwen tools/chat failed"
curl --noproxy '*' -fsS --max-time 5 http://127.0.0.1:8766/api/state | python3 -I -c '
import json,sys
s=json.load(sys.stdin)
assert any(x.get("id")=="qwen3.8-flash-next-uncensored" for x in s.get("models",[])), "room Qwen missing"
' || fail "room does not list Qwen"
q4_untouched
systemctl disable "$OLD"
systemctl enable "$NEW"
done_ok=1
trap - EXIT INT TERM
echo "SUCCESS: Q4 NUMA0 unchanged; Qwen NUMA1 warm; MAG gateway/room/structured tools tested."
echo "Rollback (idle only): sudo bash ops/qwen/cutover.sh rollback"
