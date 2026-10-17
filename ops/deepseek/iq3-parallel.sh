#!/usr/bin/env bash
# Install parallel IQ3 on NUMA1. Q4 mag-llm.service is NEVER touched.
# Gateway install may restart :8080 between conversations. It routes Q4 and IQ3
# by model id and does not restart either llama-server.
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
ACTION=${1:-preflight}
BACK=/var/lib/mag-llm/iq3-parallel-rollback
IQUNIT=/etc/systemd/system/mag-llm-iq3.service
IQGUARD=/etc/systemd/system/mag-llm-iq3.service.d/90-sequential-hotswap-required.conf
Q4GUARD=/etc/systemd/system/mag-llm.service.d/91-sequential-switch.conf
AUNIT=/etc/systemd/system/mag-audit-gateway.service
ABIN=/opt/mag-audit/bin/mag-audit-gateway
LIB=/usr/local/lib/mag-deepseek
fail(){ echo "IQ3 parallel: $*" >&2;exit 4; }
[[ $EUID -eq 0 ]] || fail "root required"
q4_health(){ curl --noproxy '*' -fsS --max-time 5 http://127.0.0.1:8090/health >/dev/null; }
backup_active(){
 for u in mag-backup-daily mag-backup-weekly mag-backup-monthly mag-backup-restore-test mag-backup-deploy mag-backup-clear;do
   systemctl is-active --quiet "$u.service" && return 0
 done
 return 1
}
original_pid(){
 systemctl show -p MainPID --value mag-llm.service
}
case "$ACTION" in
 status)
   systemctl status --no-pager mag-llm-iq3.service || true
   echo "Q4 PID $(original_pid)"
   exit 0;;
 rollback)
   [[ -f "$BACK/READY" && -f "$BACK/gateway.unit" ]] || fail "no complete snapshot"
   # This does not stop or restart Q4, and never runs a sequential model swap.
   systemctl stop mag-llm-iq3.service || true
   install -m 0644 "$BACK/gateway.unit" "$AUNIT"
   [[ ! -e "$BACK/gateway.bin" ]] || install -m 0755 "$BACK/gateway.bin" "$ABIN"
   [[ ! -e "$BACK/iq3.unit" ]] || install -m 0644 "$BACK/iq3.unit" "$IQUNIT"
   [[ ! -e "$BACK/iq3.guard" ]] || install -D -m 0644 "$BACK/iq3.guard" "$IQGUARD"
   [[ ! -e "$BACK/q4.guard" ]] || install -D -m 0644 "$BACK/q4.guard" "$Q4GUARD"
   systemctl daemon-reload
   # Re-enable the old supervisor only when the original gateway actually
   # depended on it, and never authorize a swap on rollback.
   if grep -Fq 'Requires=mag-model-supervisor.service' "$BACK/gateway.unit";then
     [[ -f "$BACK/supervisor.unit" && -f "$BACK/supervisor.bin" ]] || fail "cannot restore legacy supervisor safely"
     rm -f /etc/systemd/system/mag-model-supervisor.service
     install -m 0644 "$BACK/supervisor.unit" /etc/systemd/system/mag-model-supervisor.service
     install -m 0755 "$BACK/supervisor.bin" /opt/mag-audit/bin/mag-model-supervisor
     systemctl daemon-reload
     systemctl start mag-model-supervisor.service || fail "legacy supervisor unavailable"
   fi
   systemctl restart mag-audit-gateway.service
   q4_health || fail "Q4 unhealthy after rollback (was never restarted)"
   echo "Original gateway/units restored; Q4 process never touched."
   exit 0;;
 preflight|apply) ;;
 *) fail "use preflight|apply|status|rollback";;
esac
[[ -x "$ROOT/build/mag-audit-gateway" ]] || fail "build native gateway first"
[[ -x /home/chris/.local/ik/8d21687cc7-mag-c96826ab/bin/llama-server ]] || fail "IK runtime missing"
[[ -r /home/chris/llama-templates/deepseek-ai-DeepSeek-V4-Flash-0731.jinja ]] || fail "chat template missing"
q4_health || fail "Q4 health failed"
before=$(original_pid)
[[ "$before" =~ ^[1-9][0-9]*$ ]] || fail "Q4 MainPID unavailable"
bash "$ROOT/ops/deepseek/numa-isolation.sh" verify || fail "Q4 must already be verified NUMA0"
[[ $(cat /sys/devices/system/node/node1/cpulist) == "14-27,42-55" ]] || fail "unexpected NUMA1 CPU topology"
systemctl is-active --quiet mag-llm-iq3.service && fail "IQ3 already running; no implicit service changes"
if systemctl is-active --quiet mag-model-supervisor.service;then
  old=$(curl --noproxy '*' -fsS --max-time 3 --unix-socket /run/mag-model-supervisor/supervisor.sock http://localhost/models) || fail "old supervisor unavailable"
  [[ "$old" == *'"active":"q4"'* && "$old" == *'"pending":""'* ]] || fail "legacy model swap pending or Q4 not selected"
fi
[[ ! -e "$Q4GUARD" ]] || cmp -s "$Q4GUARD" "$ROOT/ops/deepseek/91-sequential-switch.conf" || fail "unknown existing Q4 guard, stop before mutation"
backup_active && fail "backup currently active, retry after its window"
# A forgotten Conflicts= would cause systemctl start IQ3 to stop Q4.
systemctl show -p Conflicts --value mag-llm.service | grep -Fq 'mag-llm-iq3.service' && fail "Q4 has unsafe conflicting unit"
systemctl show -p Conflicts --value mag-llm-iq3.service | grep -Fq 'mag-llm.service' && fail "IQ3 has unsafe conflict"
[[ -e "$IQGUARD" ]] && grep -Fqx 'ExecStartPre=/usr/bin/false' "$IQGUARD" || [[ ! -e "$IQGUARD" ]] || fail "unexpected IQ3 guard"
# Full ~104GB SHA read with idle I/O priority; never during backups.
ionice -c3 nice -n 19 env MAG_IQ3_PINNED_MANIFEST="$ROOT/ops/deepseek/iq3.sha256" \
  bash "$ROOT/ops/deepseek/verify-iq3.sh" >/dev/null || fail "four pinned SHA256 hashes fail"
read -r free file shmem < <(awk '$3=="MemFree:"{f=$4} $3=="FilePages:"{p=$4} $3=="Shmem:"{s=$4} END{print f+0,p+0,s+0}' /sys/devices/system/node/node1/meminfo)
usable=$(( free + (file>shmem ? file-shmem : 0) ))
(( usable >= 188*1024*1024 )) || fail "NUMA1 RAM headroom below 188GiB"
[[ "$ACTION" == preflight ]] && { echo "PASS: Q4 PID=$before stays live, IQ3 SHA/NUMA1/backup gates passed";exit 0; }
[[ ! -e "$BACK/READY" ]] || fail "previous staging exists; rollback first"
install -d -m 0700 "$BACK" "$LIB" /opt/mag-audit/bin
install -m 0644 "$AUNIT" "$BACK/gateway.unit"
[[ ! -e "$ABIN" ]] || install -m 0755 "$ABIN" "$BACK/gateway.bin"
[[ ! -e /etc/systemd/system/mag-model-supervisor.service ]] || install -m 0644 /etc/systemd/system/mag-model-supervisor.service "$BACK/supervisor.unit"
[[ ! -e /opt/mag-audit/bin/mag-model-supervisor ]] || install -m 0755 /opt/mag-audit/bin/mag-model-supervisor "$BACK/supervisor.bin"
[[ ! -e "$IQUNIT" ]] || install -m 0644 "$IQUNIT" "$BACK/iq3.unit"
[[ ! -e "$IQGUARD" ]] || install -m 0644 "$IQGUARD" "$BACK/iq3.guard"
[[ ! -e "$Q4GUARD" ]] || install -m 0644 "$Q4GUARD" "$BACK/q4.guard"
touch "$BACK/READY";chmod 0600 "$BACK/READY"
install -d -o chris -g chris -m 0700 /var/lib/mag-llm/slots/iq3
install -m 0644 "$ROOT/ops/deepseek/iq3.sha256" "$LIB/iq3.sha256"
install -m 0755 "$ROOT/ops/deepseek/verify-iq3.sh" "$LIB/verify-iq3.sh"
install -m 0755 "$ROOT/ops/deepseek/start-iq3.sh" "$LIB/start-iq3.sh"
install -m 0644 "$ROOT/ops/deepseek/mag-llm-iq3.service" "$IQUNIT"
# #33 startup veto applied only to the retired sequential service.
[[ ! -e "$IQGUARD" ]] || rm "$IQGUARD"
# The temporary SHA skip blanks ExecStartPre. The stamp check replaces it.
SKIP=/etc/systemd/system/mag-llm-iq3.service.d/99-temp-skip-sha.conf
if [[ -f "$SKIP" ]]; then
  grep -Fq 'ExecStartPre=' "$SKIP" || fail "unexpected IQ3 SHA skip drop-in"
  rm -f "$SKIP"
fi
# Q4 manual-stop override from retired #31 is not required for parallel operation.
if [[ -e "$Q4GUARD" ]];then
  cmp -s "$Q4GUARD" "$ROOT/ops/deepseek/91-sequential-switch.conf" || fail "unknown Q4 guard, inspect manually"
  rm "$Q4GUARD"
fi
install -m 0644 "$ROOT/ops/audit/systemd/mag-audit-gateway.service" "$AUNIT"
newbin=$(mktemp /opt/mag-audit/bin/.mag-audit-gateway.XXXXXXXX)
install -m 0755 "$ROOT/build/mag-audit-gateway" "$newbin"
mv -f "$newbin" "$ABIN"
systemctl daemon-reload
# Gateway has no Requires=supervisor now. Only gateway restarts, never the model.
systemctl restart mag-audit-gateway.service
curl --noproxy '*' -fsS --max-time 5 http://127.0.0.1:8080/audit/v1/health >/dev/null || fail "gateway health failed; execute rollback"
q4_health || fail "Q4 unhealthy after staging"
[[ "$(original_pid)" == "$before" ]] || fail "Q4 PID changed unexpectedly, inspect host"
systemctl disable --now mag-model-supervisor.service || fail "cannot retire old stop/start supervisor"
# Remove executable model-mutation entrypoint completely, not just its UI.
rm -f /etc/systemd/system/mag-model-supervisor.service
ln -s /dev/null /etc/systemd/system/mag-model-supervisor.service
rm -f /opt/mag-audit/bin/mag-model-supervisor
systemctl daemon-reload
systemctl is-active --quiet mag-model-supervisor.service && fail "old supervisor remains active"
# Important: IQ3 remains stopped. Ali explicitly starts it after staging.
systemctl is-active --quiet mag-llm-iq3.service && fail "IQ3 unexpectedly started"
echo "Staged successfully. Q4 unchanged PID=$before. Ali may start mag-llm-iq3.service after checks."
