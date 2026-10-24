#!/usr/bin/env bash
# Static unit/start checks + cutover success/rollback paths with mocked systemctl/curl.
set -euo pipefail
refute(){ if "$@"; then echo "unexpected match: $*" >&2; exit 1; fi; }
cd "$(dirname "$0")/../../.."
bash -n ops/ornith/*.sh
# Per instance: port 8190+N, 7 physical cores, local NUMA memory only.
for spec in '1 0-6 0' '2 7-13 0' '3 14-20 1' '4 21-27 1'; do
  read -r n cpus node <<<"$spec"
  grep -Fxq "  $n) CPUS=$cpus NODE=$node ;;" ops/ornith/start.sh
  grep -Fxq "CPUAffinity=$cpus" ops/ornith/instances/$n.conf
  grep -Fxq "AllowedCPUs=$cpus" ops/ornith/instances/$n.conf
  grep -Fxq "NUMAMask=$node" ops/ornith/instances/$n.conf
done
grep -Fq 'PORT=$((8190 + N))' ops/ornith/start.sh
grep -Fq -- '--physcpubind=$CPUS --membind=$NODE' ops/ornith/start.sh
for flag in '--alias ornith-1.5-35b-a3b' '--ctx-size 32768 --parallel 1' '--threads 7 --threads-batch 7 --numa numactl' \
  '--load-mode mlock --jinja --reasoning-format auto --reasoning off' '--cache-ram 4096 --ctx-checkpoints 8' \
  '--slot-save-path "/var/lib/mag-llm/slots/ornith$N" --no-ui' 'qwen4exp/$REV/bin/llama-server' \
  'Ornith-1.5-35B-A3B-Q4_K_M.gguf' 'reclaimable RAM below'; do
  grep -Fq -- "$flag" ops/ornith/start.sh
done
grep -Fq 'host 127.0.0.1' ops/ornith/start.sh
for line in 'User=chris' 'LimitMEMLOCK=infinity' 'NUMAPolicy=bind' 'ExecStart=/usr/local/lib/mag-ornith/start.sh %i' 'Conflicts=mag-llm-qwen.service'; do
  grep -Fxq "$line" 'ops/ornith/mag-llm-ornith@.service'
done
grep -Fq 12d8d5c01bae7f23ea4822b2f96ba069d531f827d02a57c31002f2f95e72614a ops/ornith/model.sha256
grep -Fq 'WANT=12d8d5c01bae7f23ea4822b2f96ba069d531f827d02a57c31002f2f95e72614a' ops/ornith/verify-model.sh
# Shared server (variant A): one model, 4 llama slots, 28 physical cores over both nodes.
for flag in '--interleave=all --physcpubind=0-27' '--port 8191 --ctx-size 131072 --parallel 4' \
  '--threads 28 --threads-batch 28 --numa distribute' '--load-mode mlock --jinja --reasoning-format auto --reasoning off' \
  '--slot-save-path /var/lib/mag-llm/slots/ornith --no-ui' 'qwen4exp/$REV/bin/llama-server' 'source-commit' \
  'Ornith-1.5-35B-A3B-Q4_K_M.gguf' 'reclaimable RAM below' 'verify-model.sh' 'legacy instance $n still running'; do
  grep -Fq -- "$flag" ops/ornith/start-shared.sh
done
for line in 'User=chris' 'LimitMEMLOCK=infinity' 'NUMAPolicy=interleave' 'NUMAMask=0-1' 'CPUAffinity=0-27' \
  'ExecStart=/usr/local/lib/mag-ornith/start-shared.sh' 'ExecStartPre=+/usr/local/lib/mag-ornith/verify-model.sh' \
  'Conflicts=mag-llm-qwen.service mag-llm-ornith@1.service mag-llm-ornith@2.service mag-llm-ornith@3.service mag-llm-ornith@4.service'; do
  grep -Fxq "$line" ops/ornith/mag-llm-ornith.service
done
grep -Fxq 'Environment=MAG_MODEL_ORNITH_PORTS=8191:0,8191:1,8191:2,8191:3' ops/audit/systemd/mag-audit-gateway.service
grep -Fq '8191-8194' ops/audit/mag-audit.nft
grep -Fq 'MAG_SECONDARY_MODEL=ornith' ops/deploy.sh
grep -Fq 'mag-llm-ornith.service' ops/deploy.sh
grep -Fq 'MAG_SECONDARY_MODEL=ornith' ops/boot/ensure-boot.sh
grep -Fq 'mag-llm-ornith.service' ops/boot/ensure-boot.sh
grep -Fq 'MAG_SECONDARY_MODEL=ornith' ops/bootstrap.sh
grep -Fq 'mag-llm-ornith.service' ops/bootstrap.sh
# Never touch mag-llm, cloudflared, s23pub; never delete Qwen files.
refute grep -Eq 'mag-llm[.]service' ops/ornith/rollback.sh
refute grep -Eq 'systemctl [a-z -]*(stop|restart|kill|disable)[a-z -]* mag-llm[.]service' ops/ornith/*.sh
refute grep -Eq 'cloudflared|s23pub|caddy|aws' ops/ornith/*.sh ops/ornith/*.service
refute grep -Eq 'rm .*qwen' ops/ornith/*.sh

[[ -x build/mag-audit-gateway ]] || { echo 'build mag-audit-gateway first' >&2; exit 1; }
run_case(){ # $1=from (qwen|instances) $2=fail shared server (0|1)
  local T; T=$(mktemp -d); trap 'rm -rf "$T"' RETURN
  local R=$T/root REV; REV=$(cat ops/qwen/runtime.sha)
  mkdir -p "$T/bin" "$R/run/lock" "$R/etc/mag-stack" "$R/opt/mag-audit/bin" "$R/etc/systemd/system" \
    "$R/home/chris/.local/qwen4exp/$REV/bin" "$R/sys/devices/system/node/node0" "$R/sys/devices/system/node/node1"
  printf '#!/bin/sh\n' > "$R/home/chris/.local/qwen4exp/$REV/bin/llama-server"
  printf '#!/bin/sh\necho old\n' > "$R/opt/mag-audit/bin/mag-audit-gateway"
  printf '[Service]\nEnvironment=MAG_MODEL_ORNITH_PORTS=8191,8192,8193,8194\n' > "$R/etc/systemd/system/mag-audit-gateway.service"
  chmod +x "$R/home/chris/.local/qwen4exp/$REV/bin/llama-server" "$R/opt/mag-audit/bin/mag-audit-gateway"
  if [[ $1 == qwen ]]; then echo MAG_SECONDARY_MODEL=qwen; else echo MAG_SECONDARY_MODEL=ornith; fi > "$R/etc/mag-stack/secondary-model.env"
  for n in 0 1; do printf 'Node %s MemFree: 104857600 kB\nNode %s FilePages: 0 kB\nNode %s Shmem: 0 kB\n' $n $n $n > "$R/sys/devices/system/node/node$n/meminfo"; done
  local failed=exit; [[ $2 == 1 ]] && failed='exit 0'
  cat > "$T/bin/systemctl" <<SH
#!/bin/sh
echo "\$*" >> "$T/log"
case "\$*" in
  "show -p MainPID --value mag-llm.service") echo 123 ;;
  show*) echo 0 ;;
  "is-failed --quiet mag-llm-ornith.service") $failed 1 ;;
  is-failed*) exit 1 ;;
esac
exit 0
SH
  local down=0; [[ $2 == 1 ]] && down=1
  cat > "$T/bin/curl" <<SH
#!/bin/sh
case "\$*" in
  *api/state*) echo '{"models":[{"id":"ornith-1.5-35b-a3b"}]}' ;;
  *:8191/health*) [ $down = 1 ] && exit 7 ;;
esac
exit 0
SH
  printf '#!/bin/sh\nexit 0\n' > "$T/bin/verify"
  chmod +x "$T/bin/"*
  local rc=0
  PATH="$T/bin:$PATH" MAG_ORNITH_ROOTFS=$R MAG_ORNITH_VERIFY=$T/bin/verify MAG_ORNITH_SLEEP=0 MAG_ORNITH_WAIT_TRIES=3 \
    bash ops/ornith/cutover.sh apply > "$T/out" 2>&1 || rc=$?
  refute grep -Eq '(stop|restart|disable|kill).* mag-llm[.]service|cloudflared|s23pub' "$T/log"
  grep -Fxq 'disable --now mag-llm-qwen.service' "$T/log"
  grep -Fxq 'disable --now mag-llm-ornith@1.service mag-llm-ornith@2.service mag-llm-ornith@3.service mag-llm-ornith@4.service' "$T/log"
  [[ -f "$R/etc/systemd/system/mag-llm-ornith.service" && -f "$R/etc/systemd/system/mag-llm-ornith@3.service.d/10-numa.conf" ]]
  [[ -x "$R/usr/local/lib/mag-ornith/start-shared.sh" && -x "$R/usr/local/lib/mag-ornith/start.sh" ]]
  if [[ $2 == 0 ]]; then
    [[ $rc == 0 ]] || { cat "$T/out"; return 1; }
    grep -Fxq MAG_SECONDARY_MODEL=ornith "$R/etc/mag-stack/secondary-model.env"
    grep -Fxq 'enable mag-llm-ornith.service' "$T/log"
    refute grep -Eq '^enable mag-llm-ornith@' "$T/log"
    grep -Fxq 'restart mag-audit-gateway.service' "$T/log"
    grep -Fxq 'Environment=MAG_MODEL_ORNITH_PORTS=8191:0,8191:1,8191:2,8191:3' "$R/etc/systemd/system/mag-audit-gateway.service"
    refute grep -Fq 'enable --now mag-llm-qwen.service' "$T/log"
    cmp -s build/mag-audit-gateway "$R/opt/mag-audit/bin/mag-audit-gateway"
    [[ ! -e $R/var/lib/mag-ornith/cutover-shared ]]
  else
    [[ $rc != 0 ]] || { cat "$T/out"; return 1; }
    grep -Fq "rolling back to $1" "$T/out"
    grep -Fxq 'disable --now mag-llm-ornith.service' "$T/log"
    grep -Fxq old <("$R/opt/mag-audit/bin/mag-audit-gateway")
    grep -Fxq 'Environment=MAG_MODEL_ORNITH_PORTS=8191,8192,8193,8194' "$R/etc/systemd/system/mag-audit-gateway.service"
    if [[ $1 == qwen ]]; then
      grep -Fxq MAG_SECONDARY_MODEL=qwen "$R/etc/mag-stack/secondary-model.env"
      grep -Fxq 'enable --now mag-llm-qwen.service' "$T/log"
    else
      grep -Fxq MAG_SECONDARY_MODEL=ornith "$R/etc/mag-stack/secondary-model.env"
      for n in 1 2 3 4; do grep -Fxq "enable mag-llm-ornith@$n.service" "$T/log"; done
      grep -Fxq 'start --no-block mag-llm-ornith@1.service mag-llm-ornith@2.service mag-llm-ornith@3.service mag-llm-ornith@4.service' "$T/log"
      refute grep -Fq 'enable --now mag-llm-qwen.service' "$T/log"
    fi
    [[ ! -e $R/var/lib/mag-ornith/cutover-shared ]]
  fi
}
run_case qwen 0
run_case instances 0
run_case qwen 1
run_case instances 1
echo 'ornith ops tests: OK'
