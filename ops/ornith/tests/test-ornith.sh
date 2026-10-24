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
# Per-node servers (variant B): node N = port 819(1+N), 14 physical cores, memory on node N, 2 slots.
for flag in '--cpunodebind="$NODE" --membind="$NODE" --physcpubind="$CPUS"' 'PORT=$((8191 + NODE))' \
  '--port "$PORT" --ctx-size 65536 --parallel 2' '--threads 14 --threads-batch 14 --numa numactl' \
  '--load-mode mlock --jinja --reasoning-format auto --reasoning off' '--cache-ram 2048 --ctx-checkpoints 8' \
  '--slot-save-path "/var/lib/mag-llm/slots/ornith-node$NODE" --no-ui' 'qwen4exp/$REV/bin/llama-server' 'source-commit' \
  'Ornith-1.5-35B-A3B-Q4_K_M.gguf' 'reclaimable RAM below 34GiB' 'verify-model.sh' 'legacy instance $n still running' \
  'Qwen still running' 'IQ3 still running'; do
  grep -Fq -- "$flag" ops/ornith/start-node.sh
done
grep -Fxq 'CPUS=$([[ $NODE == 0 ]] && echo 0-13 || echo 14-27)' ops/ornith/start-node.sh
for spec in '0 0-13' '1 14-27'; do
  read -r node cpus <<<"$spec"
  for line in "CPUAffinity=$cpus" "AllowedCPUs=$cpus" 'NUMAPolicy=bind' "NUMAMask=$node"; do
    grep -Fxq "$line" ops/ornith/nodes/$node.conf
  done
done
for line in 'User=chris' 'LimitMEMLOCK=infinity' 'MemoryHigh=30G' 'MemoryMax=34G' 'MemorySwapMax=0' \
  'ExecStart=/usr/local/lib/mag-ornith/start-node.sh %i' 'ExecStartPre=+/usr/local/lib/mag-ornith/verify-model.sh' \
  'ReadWritePaths=-/var/lib/mag-llm/slots/ornith-node%i' \
  'Conflicts=mag-llm-qwen.service mag-llm-ornith@1.service mag-llm-ornith@2.service mag-llm-ornith@3.service mag-llm-ornith@4.service'; do
  grep -Fxq "$line" 'ops/ornith/mag-llm-ornith-node@.service'
done
grep -Fxq 'Environment=MAG_MODEL_ORNITH_PORTS=8191:0,8192:0,8191:1,8192:1' ops/audit/systemd/mag-audit-gateway.service
grep -Fq '8191-8194' ops/audit/mag-audit.nft
grep -Fq 'MAG_SECONDARY_MODEL=ornith' ops/deploy.sh
grep -Fq 'mag-llm-ornith-node@' ops/deploy.sh
grep -Fq 'MAG_SECONDARY_MODEL=ornith' ops/boot/ensure-boot.sh
grep -Fq 'mag-llm-ornith-node@' ops/boot/ensure-boot.sh
grep -Fq 'MAG_SECONDARY_MODEL=ornith' ops/bootstrap.sh
grep -Fq 'mag-llm-ornith-node@' ops/bootstrap.sh
# Never touch mag-llm, cloudflared, s23pub; never delete Qwen files.
refute grep -Eq 'mag-llm[.]service' ops/ornith/rollback.sh
refute grep -Eq 'systemctl [a-z -]*(stop|restart|kill|disable)[a-z -]* mag-llm[.]service' ops/ornith/*.sh
refute grep -Eq 'cloudflared|s23pub|caddy|aws' ops/ornith/*.sh ops/ornith/*.service
refute grep -Eq 'rm .*qwen' ops/ornith/*.sh

[[ -x build/mag-audit-gateway ]] || { echo 'build mag-audit-gateway first' >&2; exit 1; }
run_case(){ # $1=from (qwen|instances) $2=fail node server 1 (0|1)
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
  "show -p MainPID --value mag-llm.service") echo ${Q4PID:-123} ;;
  show*) echo 0 ;;
  "is-failed --quiet mag-llm-ornith-node@1.service") $failed 1 ;;
  is-failed*) exit 1 ;;
esac
exit 0
SH
  local down=0; [[ $2 == 1 ]] && down=1
  cat > "$T/bin/curl" <<SH
#!/bin/sh
case "\$*" in
  *api/state*) echo '{"models":[{"id":"ornith-1.5-35b-a3b"}]}' ;;
  *:819[12]/health*) grep -q '^start --no-block mag-llm-ornith-node' "$T/log" || exit 7
    case "\$*" in *:8192/health*) [ $down = 1 ] && exit 7 ;; esac ;;
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
  [[ -f "$R/etc/systemd/system/mag-llm-ornith-node@.service" && -f "$R/etc/systemd/system/mag-llm-ornith@3.service.d/10-numa.conf" ]]
  grep -Fxq 'NUMAMask=1' "$R/etc/systemd/system/mag-llm-ornith-node@1.service.d/10-numa.conf"
  [[ -x "$R/usr/local/lib/mag-ornith/start-node.sh" && -x "$R/usr/local/lib/mag-ornith/start.sh" ]]
  # Legacy instances 1/2 (ports 8191/8192) stop before any node server starts.
  local stop_at start_at
  stop_at=$(grep -nFx 'disable --now mag-llm-ornith@1.service mag-llm-ornith@2.service mag-llm-ornith@3.service mag-llm-ornith@4.service' "$T/log" | head -1 | cut -d: -f1)
  start_at=$(grep -nFx 'start --no-block mag-llm-ornith-node@0.service mag-llm-ornith-node@1.service' "$T/log" | head -1 | cut -d: -f1)
  (( stop_at < start_at ))
  if [[ $2 == 0 ]]; then
    [[ $rc == 0 ]] || { cat "$T/out"; return 1; }
    grep -Fxq MAG_SECONDARY_MODEL=ornith "$R/etc/mag-stack/secondary-model.env"
    grep -Fxq 'enable mag-llm-ornith-node@0.service mag-llm-ornith-node@1.service' "$T/log"
    refute grep -Eq '^enable mag-llm-ornith@' "$T/log"
    grep -Fxq 'restart mag-audit-gateway.service' "$T/log"
    grep -Fxq 'Environment=MAG_MODEL_ORNITH_PORTS=8191:0,8192:0,8191:1,8192:1' "$R/etc/systemd/system/mag-audit-gateway.service"
    refute grep -Fq 'enable --now mag-llm-qwen.service' "$T/log"
    cmp -s build/mag-audit-gateway "$R/opt/mag-audit/bin/mag-audit-gateway"
    [[ ! -e $R/var/lib/mag-ornith/cutover-numa ]]
  else
    [[ $rc != 0 ]] || { cat "$T/out"; return 1; }
    grep -Fq "rolling back to $1" "$T/out"
    grep -Fxq 'disable --now mag-llm-ornith-node@0.service mag-llm-ornith-node@1.service' "$T/log"
    # Node servers are stopped before the legacy instances (same ports) start again.
    if [[ $1 == instances ]]; then
      local nstop lstart
      nstop=$(grep -nFx 'disable --now mag-llm-ornith-node@0.service mag-llm-ornith-node@1.service' "$T/log" | tail -1 | cut -d: -f1)
      lstart=$(grep -nFx 'start --no-block mag-llm-ornith@1.service mag-llm-ornith@2.service mag-llm-ornith@3.service mag-llm-ornith@4.service' "$T/log" | cut -d: -f1)
      (( nstop < lstart ))
    fi
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
    [[ ! -e $R/var/lib/mag-ornith/cutover-numa ]]
  fi
}
run_case qwen 0
run_case instances 0
run_case qwen 1
run_case instances 1
# DeepSeek retired: cutover must also work with mag-llm inactive (MainPID 0).
Q4PID=0 run_case qwen 0
Q4PID=0 run_case qwen 1
echo 'ornith ops tests: OK'
