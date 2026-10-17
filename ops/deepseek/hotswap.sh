#!/usr/bin/env bash
# Stage sequential Q4/IQ3 model supervisor. NO llama-server stop/start/restart.
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
ACTION=$1
BACK=/var/lib/mag-llm/hotswap-rollback
AUNIT=/etc/systemd/system/mag-audit-gateway.service
ABIN=/opt/mag-audit/bin/mag-audit-gateway
SBIN=/opt/mag-audit/bin/mag-model-supervisor
SUNIT=/etc/systemd/system/mag-model-supervisor.service
NFT=/etc/nftables.d/mag-audit.nft
IQUNIT=/etc/systemd/system/mag-llm-iq3.service
STOPGUARD=/etc/systemd/system/mag-llm.service.d/91-sequential-switch.conf
IQ_GUARD=/etc/systemd/system/mag-llm-iq3.service.d/90-sequential-hotswap-required.conf
LIB=/usr/local/lib/mag-deepseek
die(){ echo "hotswap: $*" >&2;exit 4; }
[[ $EUID == 0 ]] || die "root required"
health(){ curl --noproxy '*' -fsS --max-time 5 "http://127.0.0.1:$1/health" >/dev/null; }
selector(){ curl --noproxy '*' -fsS --max-time 5 --unix-socket /run/mag-model-supervisor/supervisor.sock http://localhost/models; }
case "$ACTION" in
status)
 for u in mag-llm mag-llm-iq3 mag-audit-gateway mag-model-supervisor mag-model-pin;do echo "$u: $(systemctl is-active "$u" 2>/dev/null || true)";done
 selector || true;exit 0;;
rollback)
 [[ -f "$BACK/READY" && -f "$BACK/gateway.unit" && -f "$BACK/audit.nft" ]] || die "rollback snapshot missing"
 if systemctl is-active --quiet mag-model-supervisor.service;then
   state=$(selector) || die "supervisor unavailable"
   [[ "$state" != *'"pending":"iq3"'* ]] || die "another switch still running"
   if [[ "$state" != *'"active":"q4"'* ]];then
     curl --noproxy '*' -fsS --max-time 5 --unix-socket /run/mag-model-supervisor/supervisor.sock \
       -X PUT -H 'Content-Type: application/json' -d '{"id":"q4"}' http://localhost/models >/dev/null || die "Q4 rollback rejected"
     healthy=0
     for n in $(seq 1 1800);do
       state=$(selector) || break
       if [[ "$state" == *'"active":"q4"'* && "$state" == *'"pending":""'* ]] && health 8090;then healthy=1;break;fi
       sleep 2
     done
     (( healthy )) || die "Q4 recovery failed; keep gateway for diagnosis"
   fi
 fi
 health 8090 || die "Q4 unhealthy; refusing old gateway"
 install -m 0644 "$BACK/gateway.unit" "$AUNIT"
 [[ ! -f "$BACK/gateway.bin" ]] || install -m 0755 "$BACK/gateway.bin" "$ABIN"
 install -m 0640 "$BACK/audit.nft" "$NFT"
 nft -c -f "$NFT" || die "rollback firewall invalid"
 nft -f "$NFT"
 systemctl daemon-reload
 systemctl restart mag-audit-gateway.service
 # Restore #33 fail-closed IQ3 lock before retiring the exclusive supervisor.
 [[ -f "$BACK/iq3.guard" ]] || die "missing original IQ3 lock, keep supervisor active"
 install -D -m 0644 "$BACK/iq3.guard" "$IQ_GUARD"
 systemctl daemon-reload
 systemctl stop mag-model-supervisor.service || true
 if [[ -f "$BACK/stop.guard" ]];then install -m 0644 "$BACK/stop.guard" "$STOPGUARD";else rm -f "$STOPGUARD";fi
 systemctl daemon-reload
 echo "Restored gateway and Q4. No unapproved Q4 restart."
 exit 0;;
preflight|apply) ;;
*) die "usage: $0 status|preflight|apply|rollback";;
esac
[[ -x "$ROOT/build/mag-audit-gateway" && -x "$ROOT/build/mag-model-supervisor" ]] || die "build native binaries first"
[[ -f "$AUNIT" && -f "$NFT" ]] || die "live gateway or firewall missing"
health 8090 || die "Q4 unhealthy"
systemctl is-active --quiet mag-llm-iq3.service && die "both models must never be resident"
systemctl is-active --quiet mag-model-pin.service && die "double pin must first be disabled by #33"
bash "$ROOT/ops/deepseek/numa-isolation.sh" verify || die "NUMA0 verification failed: Ali must finish #33 manual restart"
# PR #33 deliberately blocks starting IQ3. This PR may replace the lock only
# after the sequential supervisor is installed and the full preflight succeeds.
[[ -f "$IQ_GUARD" ]] && grep -Fxq 'ExecStartPre=/usr/bin/false' "$IQ_GUARD" ||
 die "missing #33 IQ3 startup lock: unsafe or incorrect deployment order"
bash "$ROOT/ops/deepseek/verify-iq3.sh" >/dev/null || die "IQ3 download/SHA incomplete"
[[ -x /home/chris/.local/ik/8d21687cc7-mag-c96826ab/bin/llama-server ]] || die "ik binary missing"
[[ -r /home/chris/llama-templates/deepseek-ai-DeepSeek-V4-Flash-0731.jinja ]] || die "chat template missing"
tmp=$(mktemp);trap 'rm -f "$tmp"' EXIT
grep -Fq 'tcp dport 8090' "$NFT" || grep -Fq 'tcp dport { 8090, 8091 }' "$NFT" || die "unexpected nft policy"
sed 's/tcp dport 8090 /tcp dport { 8090, 8091 } /' "$NFT" > "$tmp"
nft -c -f "$tmp" || die "nft preflight failed"
[[ "$ACTION" == preflight ]] && { echo "PASS. No host services changed.";exit 0; }
[[ ! -e "$BACK/READY" ]] || die "already staged (rollback first)"
install -d -m 0700 "$BACK"
cp -p "$AUNIT" "$BACK/gateway.unit";cp -p "$NFT" "$BACK/audit.nft"
[[ ! -e "$ABIN" ]] || cp -p "$ABIN" "$BACK/gateway.bin"
[[ ! -e "$STOPGUARD" ]] || cp -p "$STOPGUARD" "$BACK/stop.guard"
cp -p "$IQ_GUARD" "$BACK/iq3.guard"
touch "$BACK/READY";chmod 0600 "$BACK/READY"
install -D -m 0644 "$ROOT/ops/deepseek/91-sequential-switch.conf" "$STOPGUARD"
install -d -o chris -g chris -m 0700 /var/lib/mag-llm/slots/iq3
install -d -m 0755 "$LIB" /opt/mag-audit/bin
install -m 0755 "$ROOT/ops/deepseek/verify-iq3.sh" "$LIB/verify-iq3.sh"
install -m 0755 "$ROOT/ops/deepseek/start-iq3.sh" "$LIB/start-iq3.sh"
install -m 0644 "$ROOT/ops/deepseek/mag-llm-iq3.service" "$IQUNIT"
# Remove the deliberate #33 ExecStartPre=/usr/bin/false lock only once the
# new IQ3 service is present, with its own SHA and Q4-off gates in place.
# The C++ supervisor is the sole normal entry point for an IQ3 start.
rm -f "$IQ_GUARD"
install -m 0644 "$ROOT/ops/deepseek/mag-model-supervisor.service" "$SUNIT"
install -m 0640 "$tmp" "$NFT"
nft -f "$NFT"
install -m 0644 "$ROOT/ops/audit/systemd/mag-audit-gateway.service" "$AUNIT"
install -m 0755 "$ROOT/build/mag-model-supervisor" "$SBIN"
install -m 0755 "$ROOT/build/mag-audit-gateway" "$ABIN"
systemctl daemon-reload
systemctl enable --now mag-model-supervisor.service
systemctl restart mag-audit-gateway.service
curl --noproxy '*' -fsS --max-time 5 http://127.0.0.1:8080/audit/v1/health >/dev/null || die "gateway failed: rollback"
health 8090 || die "Q4 unexpectedly unhealthy"
systemctl is-active --quiet mag-llm-iq3.service && die "IQ3 unexpectedly started"
echo "Sequential Hot-Swap staged. Q4 still resident NUMA0, IQ3 remains stopped."
