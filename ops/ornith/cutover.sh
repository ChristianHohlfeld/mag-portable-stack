#!/usr/bin/env bash
# Operator-only: NUMA1 Qwen -> 4x Ornith-1.5-35B-A3B (2 per NUMA node) for all room bots.
# Only Qwen/Ornith units, gateway and room change; mag-llm (DeepSeek Q4 :8090) and tunnels stay untouched.
# Any failure after Qwen stops rolls back automatically (ops/ornith/rollback.sh).
# Usage: cutover.sh check|apply   (MAG_ORNITH_ROOTFS only for tests)
set -Eeuo pipefail
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
R=${MAG_ORNITH_ROOTFS:-}
ACTION=${1:-check}
CONF=$R/etc/mag-stack/secondary-model.env
LIB=$R/usr/local/lib/mag-ornith
UNITS=$R/etc/systemd/system
GATE=$R/opt/mag-audit/bin/mag-audit-gateway
SNAP=$R/var/lib/mag-ornith/cutover
WAIT_TRIES=${MAG_ORNITH_WAIT_TRIES:-180}
fail(){ echo "Ornith cutover: $*" >&2; exit 4; }
[[ $EUID == 0 || -n $R ]] || fail 'root required'
[[ $ACTION == check || $ACTION == apply ]] || fail 'usage: cutover.sh check|apply'
exec 8>"$R/run/lock/mag-private-deploy.lock"
flock -n 8 || fail 'MAG deploy/cutover already in progress'
health(){ curl --noproxy '*' -fsS -o /dev/null --max-time 5 "http://127.0.0.1:$1/health"; }
pid(){ systemctl show -p MainPID --value "$1" 2>/dev/null || true; }
node_usable_kib(){
  awk '$3=="MemFree:"{f=$4} $3=="FilePages:"{p=$4} $3=="Shmem:"{s=$4} END{print f+(p>s?p-s:0)}' "$R/sys/devices/system/node/node$1/meminfo"
}
Q4_PID=$(pid mag-llm.service)
[[ $Q4_PID =~ ^[1-9][0-9]*$ ]] || fail 'mag-llm not running'
q4_untouched(){ [[ $(pid mag-llm.service) == "$Q4_PID" ]] || fail 'mag-llm PID changed'; }
[[ -x $ROOT/build/mag-audit-gateway ]] || fail 'build the Ornith-aware native gateway first'
[[ -x $GATE ]] || fail 'installed gateway missing'
[[ ! -s $CONF ]] || grep -Eqx 'MAG_SECONDARY_MODEL=(iq3|qwen)' "$CONF" || fail 'secondary config unexpected'
[[ ! -e $SNAP ]] || fail "previous snapshot $SNAP exists; run rollback.sh first"
REV=$(cat "$ROOT/ops/qwen/runtime.sha")
[[ -x $R/home/chris/.local/qwen4exp/$REV/bin/llama-server ]] || fail 'Qwen/Ornith llama.cpp runtime missing'
# Install launcher, units and pin (inactive until started below).
install -d -m 0755 "$LIB" "$R/etc/mag-ornith"
install -m 0755 "$ROOT/ops/ornith/start.sh" "$ROOT/ops/ornith/verify-model.sh" "$LIB/"
install -m 0644 "$ROOT/ops/qwen/runtime.sha" "$LIB/runtime.sha"
install -m 0644 "$ROOT/ops/ornith/model.sha256" "$R/etc/mag-ornith/model.sha256"
install -m 0644 "$ROOT/ops/ornith/mag-llm-ornith@.service" "$UNITS/mag-llm-ornith@.service"
for n in 1 2 3 4; do
  install -d -m 0755 "$UNITS/mag-llm-ornith@$n.service.d"
  install -m 0644 "$ROOT/ops/ornith/instances/$n.conf" "$UNITS/mag-llm-ornith@$n.service.d/10-numa.conf"
done
# Full model SHA before anything is stopped (root writes the verify stamp).
"${MAG_ORNITH_VERIFY:-$LIB/verify-model.sh}" || fail 'model SHA-256 mismatch'
(( $(node_usable_kib 0) >= 2*26*1024*1024 )) || fail 'NUMA0 RAM below 2x26GiB'
if [[ $ACTION == check ]]; then echo 'PASS: Ornith cutover ready; mag-llm untouched'; exit 0; fi

install -d -m 0700 "$SNAP"
if [[ -f $CONF ]]; then cp -p "$CONF" "$SNAP/secondary.env"; else touch "$SNAP/no-conf"; fi
install -m 0755 "$GATE" "$SNAP/gateway.bin"
done_ok=0
on_exit(){
  local rc=$?
  trap - EXIT INT TERM
  if (( done_ok == 0 )); then
    echo "Ornith cutover failed ($rc); rolling back to Qwen" >&2
    MAG_ORNITH_ROOTFS=$R bash "$ROOT/ops/ornith/rollback.sh" || echo 'MANUAL recovery required' >&2
  fi
  exit "$rc"
}
trap on_exit EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
systemctl disable --now mag-llm-qwen.service
[[ $(pid mag-llm-qwen.service) == 0 ]] || fail 'Qwen still has a live PID'
(( $(node_usable_kib 1) >= 2*26*1024*1024 )) || fail 'NUMA1 RAM below 2x26GiB after stopping Qwen'
systemctl daemon-reload
for n in 1 2 3 4; do systemctl enable "mag-llm-ornith@$n.service"; done
systemctl start --no-block mag-llm-ornith@{1,2,3,4}.service
for n in 1 2 3 4; do
  ok=0
  for ((i=0; i<WAIT_TRIES; i++)); do
    if health $((8190 + n)); then ok=1; break; fi
    systemctl is-failed --quiet "mag-llm-ornith@$n.service" && fail "instance $n failed"
    sleep "${MAG_ORNITH_SLEEP:-10}"
  done
  (( ok )) || fail "instance $n not healthy"
done
q4_untouched
install -m 0755 "$ROOT/build/mag-audit-gateway" "$GATE.next"
mv -f "$GATE.next" "$GATE"
install -m 0644 "$ROOT/ops/audit/systemd/mag-audit-gateway.service" "$UNITS/mag-audit-gateway.service"
install -d -m 0755 "$(dirname "$CONF")"
printf 'MAG_SECONDARY_MODEL=ornith\n' > "$CONF.next"
chmod 0644 "$CONF.next"
mv -f "$CONF.next" "$CONF"
systemctl daemon-reload
systemctl restart mag-audit-gateway.service
systemctl restart mag-120.service
ok=0
for ((i=0; i<30; i++)); do
  if curl --noproxy '*' -fsS --max-time 5 http://127.0.0.1:8766/api/state | python3 -I -c '
import json,sys
s=json.load(sys.stdin)
assert any(m.get("id")=="ornith-1.5-35b-a3b" for m in s.get("models",[]))
' 2>/dev/null; then ok=1; break; fi
  sleep "${MAG_ORNITH_SLEEP:-2}"
done
(( ok )) || fail 'MAG room does not advertise Ornith'
for n in 1 2 3 4; do health $((8190 + n)) || fail "instance $n unhealthy after routing"; done
q4_untouched
done_ok=1
trap - EXIT INT TERM
echo 'SUCCESS: 4x Ornith live (8191-8194), Qwen disabled (files kept), mag-llm untouched.'
