#!/bin/bash
# Direkt-Install 3.8: ik -rtr + NUMA-lokal (MAG_NUMA_LOCAL=16), ohne A/B-Matrix.
set -euo pipefail
SRC=/home/chris/mag-stack
source $SRC/ops/deepseek/llm-config.sh
UNIT=/etc/systemd/system/mag-llm.service
IK_NEW=$(ls -t /home/chris/.cache/mag-stack/ik-*/bin/llama-server /home/chris/*/ik*/build/bin/llama-server 2>/dev/null | head -1 || true)
IK_NEW=${IK_NEW_OVERRIDE:-$IK_NEW}
echo "IK_NEW=$IK_NEW"; [ -x "$IK_NEW" ]
rm -f /etc/systemd/system/mag-llm.service.d/60-mag-variant.conf /etc/systemd/system/mag-llm.service.d/50-ik.conf
export ENGINE=ik IK_BIN=$IK_NEW IK_RTR=1 IK_ENV="MAG_NUMA_LOCAL=16" IK_ARGS=- NUMA_ARGS=- LLAMA_NUMA=- THREADS=16 THREADS_BATCH=28
llm_config_load; llm_config_save
render_unit "$UNIT"
install -d /run/systemd/system/mag-llm.service.d
printf '[Unit]\nRefuseManualStop=no\n' > /run/systemd/system/mag-llm.service.d/99-upgrade.conf; systemctl daemon-reload
systemctl restart mag-llm.service
rm -f /run/systemd/system/mag-llm.service.d/99-upgrade.conf; systemctl daemon-reload
for i in $(seq 1 720); do
  c=$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 http://127.0.0.1:8090/health || true)
  [ "$c" = 200 ] && { echo "ready nach $((i*5)) s"; break; }
  a=$(awk '/^MemAvailable:/{print int($2/1048576)}' /proc/meminfo)
  (( a < 40 )) && { echo "WATCHDOG avail=$a -> rollback"; bash $SRC/ops/deepseek/upgrade-3.8.sh --rollback; exit 1; }
  sleep 5
done
