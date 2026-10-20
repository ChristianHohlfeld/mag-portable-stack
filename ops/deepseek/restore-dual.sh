#!/usr/bin/env bash
# Explicit, owner-operated recovery AFTER inspecting diagnose-ai.sh.
# Never called by deploy/update/bootstrap. Does not erase model files.
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
mode=${1:-check}
fail(){ echo "restore-dual: $*" >&2; exit 4; }
health(){ curl --noproxy '*' -fsS --max-time 4 "http://127.0.0.1:$1/health" >/dev/null; }
pid(){ systemctl show -p MainPID --value "$1.service" 2>/dev/null || true; }
case "$mode" in check|apply) ;; *) fail 'Usage: check | apply (explicit reload)' ;; esac
[[ $EUID -eq 0 ]] || fail 'run as root on MAG console'
[[ "$(cat /sys/devices/system/node/node0/cpulist 2>/dev/null)" == '0-13,28-41' ]] || fail 'unknown node0 topology'
[[ "$(cat /sys/devices/system/node/node1/cpulist 2>/dev/null)" == '14-27,42-55' ]] || fail 'unknown node1 topology'
health 8090 || fail 'Q4 must be healthy before any change'
systemctl is-active --quiet mag-backup-deploy.service && fail 'deploy/backup is busy'
systemctl is-active --quiet mag-backup-daily.service && fail 'backup is busy'
systemctl is-active --quiet mag-backup-restore-test.service && fail 'backup restore is busy'
state=$(curl --noproxy '*' -fsS --max-time 5 http://127.0.0.1:8766/api/state) || fail 'cannot confirm room is idle'
if ! python3 -I -c 'import json,sys; d=json.load(sys.stdin); assert not d.get("busy") and not d.get("pending") and not d.get("queue") and not d.get("busy_bots") and not d.get("jobruns"),"MAG has pending work"' <<<"$state";then
  fail 'room active / busy / pending (never interrupt a coding task)'
fi
before=$(pid mag-llm)
[[ $before =~ ^[1-9][0-9]*$ ]] || fail 'Q4 has no live PID'
if [[ "$mode" == check ]]; then
  echo "Room idle, Q4 healthy. No changes made."
  echo "Next: review ops/perf/diagnose-ai.sh, then MAG_RECOVERY_ALLOW_RELOAD=YES $0 apply."
  exit 0
fi
[[ "${MAG_RECOVERY_ALLOW_RELOAD:-}" == YES ]] || fail 'explicit reload consent required'
# This can be lengthy and is therefore NEVER part of a routine deploy.
# Reuse the existing NUMA preflight+rollback-guard code. Fail closed if its
# rollback snapshot exists or headroom is inadequate; never overwrite it.
bash "$ROOT/ops/deepseek/numa-isolation.sh" stage || fail 'node0 stage refused; Q4 unchanged'
systemctl daemon-reload
# Q4 has a dedicated Restart=always; RefuseManualStop prohibits systemctl restart.
bash "$ROOT/ops/deepseek/warm.sh" restart-llm || fail 'Q4 reload failed'
ready=0
for i in {1..180}; do
  if health 8090; then ready=1; break; fi
  sleep 5
done
[[ $ready == 1 ]] || fail 'Q4 has not become healthy; inspect model-gate and rollback snapshot'
after=$(pid mag-llm)
[[ $after =~ ^[1-9][0-9]*$ && "$after" != "$before" ]] || fail 'Q4 PID unchanged'
bash "$ROOT/ops/deepseek/numa-isolation.sh" verify || fail 'Q4 locality verification failed; do not start IQ3'
[[ -x /usr/local/lib/mag-deepseek/start-iq3.sh ]] || fail 'IQ3 service not installed'
# No extra model process may consume Q4 node0. IQ3 start-iq3.sh itself enforces
# >=188 GiB available/reclaimable on node1, model hashes and backup locks.
systemctl enable mag-llm-iq3.service
systemctl start mag-llm-iq3.service || fail 'IQ3 cannot start; Q4 preserved'
ready=0
for i in {1..180}; do
  if health 8091; then ready=1; break; fi
  sleep 5
done
[[ $ready == 1 ]] || fail 'IQ3 did not become healthy, inspect journal; Q4 preserved'
echo "Q4 verified on NUMA0 ($after); IQ3 healthy on NUMA1; no room messages deleted."
