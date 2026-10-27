#!/usr/bin/env bash
# Operator-only: Qwen or the 4x Ornith instances -> TWO Ornith servers, one per NUMA node
# (variant B: mag-llm-ornith-node@0 :8191, @1 :8192, --parallel 2, two room bots each).
# Ports 8191/8192 are also legacy instances 1/2: the old layout is stopped (PID 0)
# before any node server starts.
# Only Qwen/Ornith units, gateway and room change; mag-llm (DeepSeek, retired, optional) and tunnels stay untouched.
# Any failure after the old layout stops rolls back automatically to that layout
# (rollback.sh instances, or rollback.sh qwen when coming from Qwen).
# Usage: cutover.sh check|apply   (MAG_ORNITH_ROOTFS only for tests)
set -Eeuo pipefail
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
R=${MAG_ORNITH_ROOTFS:-}
ACTION=${1:-check}
CONF=$R/etc/mag-stack/secondary-model.env
LIB=$R/usr/local/lib/mag-ornith
UNITS=$R/etc/systemd/system
GATE=$R/opt/mag-audit/bin/mag-audit-gateway
SNAP=$R/var/lib/mag-ornith/cutover-numa
NODES=(mag-llm-ornith-node@0.service mag-llm-ornith-node@1.service)
PORTS=8191:0,8192:0,8191:1,8192:1
WAIT_TRIES=${MAG_ORNITH_WAIT_TRIES:-180}
INSTANCES=(mag-llm-ornith@1.service mag-llm-ornith@2.service mag-llm-ornith@3.service mag-llm-ornith@4.service)
fail(){ echo "Ornith cutover: $*" >&2; exit 4; }
[[ $EUID == 0 || -n $R ]] || fail 'root required'
[[ $ACTION == check || $ACTION == apply ]] || fail 'usage: cutover.sh check|apply'
# Archived cutover requires its matching four-slot gateway. Refuse before
# installing files or touching services on the current DeepSeek deployment.
grep -Fxq "Environment=MAG_MODEL_ORNITH_PORTS=$PORTS" "$ROOT/ops/audit/systemd/mag-audit-gateway.service" || fail 'legacy gateway required; use ops/room/install.sh for the configured model'
# shellcheck source=../deploy-guard.sh
source "$ROOT/ops/deploy-guard.sh"
export MAG_DEPLOY_LOCK="$R/run/lock/mag-private-deploy.lock"
export MAG_DEPLOY_LOCK_MESSAGE='MAG deploy/cutover already in progress'
export MAG_DEPLOY_LOCK_EXIT=4
mag_deploy_lock "$@"
health(){ curl --noproxy '*' -fsS -o /dev/null --max-time 5 "http://127.0.0.1:$1/health"; }
pid(){ systemctl show -p MainPID --value "$1" 2>/dev/null || true; }
node_usable_kib(){
  awk '$3=="MemFree:"{f=$4} $3=="FilePages:"{p=$4} $3=="Shmem:"{s=$4} END{print f+(p>s?p-s:0)}' "$R/sys/devices/system/node/node$1/meminfo"
}
Q4_PID=$(pid mag-llm.service)
# DeepSeek (mag-llm) is retired and may be inactive (PID 0); if it still runs it stays untouched.
[[ $Q4_PID =~ ^[0-9]+$ ]] || Q4_PID=0
(( Q4_PID )) || echo 'INFO: mag-llm (DeepSeek, retired) not running; continuing'
q4_untouched(){ [[ $(pid mag-llm.service) == "$Q4_PID" ]] || fail 'mag-llm PID changed'; }
[[ -x $ROOT/build/mag-audit-gateway ]] || fail 'build the Ornith-aware native gateway first'
[[ -x $GATE ]] || fail 'installed gateway missing'
[[ ! -s $CONF ]] || grep -Eqx 'MAG_SECONDARY_MODEL=(iq3|qwen|ornith)' "$CONF" || fail 'secondary config unexpected'
[[ ! -e $SNAP ]] || fail "previous snapshot $SNAP exists; run rollback.sh first"
# Previous layout decides the automatic rollback target.
FROM=qwen
grep -Fxq 'MAG_SECONDARY_MODEL=ornith' "$CONF" 2>/dev/null && FROM=instances
REV=$(cat "$ROOT/ops/qwen/runtime.sha")
[[ -x $R/home/chris/.local/qwen4exp/$REV/bin/llama-server ]] || fail 'Qwen/Ornith llama.cpp runtime missing'
# Install launchers and units (inactive until started below). Legacy instance files stay for rollback.
install -d -m 0755 "$LIB" "$R/etc/mag-ornith"
install -m 0755 "$ROOT/ops/ornith/start.sh" "$ROOT/ops/ornith/start-node.sh" "$ROOT/ops/ornith/verify-model.sh" "$LIB/"
install -m 0644 "$ROOT/ops/qwen/runtime.sha" "$LIB/runtime.sha"
install -m 0644 "$ROOT/ops/ornith/model.sha256" "$R/etc/mag-ornith/model.sha256"
install -m 0644 "$ROOT/ops/ornith/mag-llm-ornith-node@.service" "$UNITS/mag-llm-ornith-node@.service"
for n in 0 1; do
  install -d -m 0755 "$UNITS/mag-llm-ornith-node@$n.service.d"
  install -m 0644 "$ROOT/ops/ornith/nodes/$n.conf" "$UNITS/mag-llm-ornith-node@$n.service.d/10-numa.conf"
done
install -m 0644 "$ROOT/ops/ornith/mag-llm-ornith@.service" "$UNITS/mag-llm-ornith@.service"
for n in 1 2 3 4; do
  install -d -m 0755 "$UNITS/mag-llm-ornith@$n.service.d"
  install -m 0644 "$ROOT/ops/ornith/instances/$n.conf" "$UNITS/mag-llm-ornith@$n.service.d/10-numa.conf"
done
# Full model SHA before anything is stopped (root writes the verify stamp).
"${MAG_ORNITH_VERIFY:-$LIB/verify-model.sh}" || fail 'model SHA-256 mismatch'
if [[ $ACTION == check ]]; then echo "PASS: Ornith per-node cutover ready (from $FROM); mag-llm untouched"; exit 0; fi

install -d -m 0700 "$SNAP"
if [[ -f $CONF ]]; then cp -p "$CONF" "$SNAP/secondary.env"; else touch "$SNAP/no-conf"; fi
install -m 0755 "$GATE" "$SNAP/gateway.bin"
[[ -f $UNITS/mag-audit-gateway.service ]] && cp -p "$UNITS/mag-audit-gateway.service" "$SNAP/gateway.service"
echo "$FROM" > "$SNAP/from"
done_ok=0
on_exit(){
  local rc=$?
  trap - EXIT INT TERM
  if (( done_ok == 0 )); then
    echo "Ornith cutover failed ($rc); rolling back to $FROM" >&2
    MAG_ORNITH_ROOTFS=$R bash "$ROOT/ops/ornith/rollback.sh" "$FROM" || echo 'MANUAL recovery required' >&2
  fi
  exit "$rc"
}
trap on_exit EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
# Disable (never delete) the old layout.
systemctl disable --now mag-llm-qwen.service
systemctl disable --now "${INSTANCES[@]}"
[[ $(pid mag-llm-qwen.service) == 0 ]] || fail 'Qwen still has a live PID'
for u in "${INSTANCES[@]}"; do [[ $(pid "$u") == 0 ]] || fail "$u still has a live PID"; done
# Ports 8191/8192 must be free (legacy instances 1/2 gone) before the node servers start.
for p in 8191 8192; do ! health $p || fail "port $p still answers after stopping the old layout"; done
# Each node server holds ~21.8 GiB RSS + 2 GiB cache-ram on its own node.
for node in 0 1; do
  (( $(node_usable_kib $node) >= 24*1024*1024 )) || fail "NUMA$node RAM below 24GiB after stopping the old layout"
done
systemctl daemon-reload
systemctl enable "${NODES[@]}"
systemctl start --no-block "${NODES[@]}"
ok=0
for ((i=0; i<WAIT_TRIES; i++)); do
  if health 8191 && health 8192; then ok=1; break; fi
  for u in "${NODES[@]}"; do systemctl is-failed --quiet "$u" && fail "$u failed"; done
  sleep "${MAG_ORNITH_SLEEP:-10}"
done
(( ok )) || fail 'node servers not healthy'
q4_untouched
install -m 0755 "$ROOT/build/mag-audit-gateway" "$GATE.next"
mv -f "$GATE.next" "$GATE"
install -m 0644 "$ROOT/ops/audit/systemd/mag-audit-gateway.service" "$UNITS/mag-audit-gateway.service"
grep -Fxq "Environment=MAG_MODEL_ORNITH_PORTS=$PORTS" "$UNITS/mag-audit-gateway.service" || fail 'gateway unit lacks per-node slots'
install -d -m 0755 "$(dirname "$CONF")"
printf 'MAG_SECONDARY_MODEL=ornith\n' > "$CONF.next"
chmod 0644 "$CONF.next"
mv -f "$CONF.next" "$CONF"
systemctl daemon-reload
systemctl restart mag-audit-gateway.service
# Room restart prewarms all four bot slots right away (per server serialized, shared prefix).
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
health 8191 && health 8192 || fail 'node servers unhealthy after routing'
q4_untouched
done_ok=1
trap - EXIT INT TERM
rm -rf -- "$SNAP"
echo 'SUCCESS: Ornith per NUMA node live (8191+8192, 2 slots each), old layout disabled (files kept), mag-llm untouched.'

