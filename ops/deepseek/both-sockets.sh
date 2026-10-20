#!/usr/bin/env bash
# One Q4 model on both sockets. Idempotent cutover, run on mag as root:
#   sudo bash ops/deepseek/both-sockets.sh
# Renders first and leaves the host untouched if the unit would be wrong.
# Then installs llm.env.mag, removes the node0 cgroup pin, stops IQ3 so
# node1 is not already full, and restarts mag-llm (loads ~144 GiB).
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
ENV_SRC=$ROOT/ops/deepseek/llm.env.mag
ENV=/etc/mag-llm/llm.env
UNIT=/etc/systemd/system/mag-llm.service
GUARD=/etc/systemd/system/mag-llm.service.d/90-node0.conf
IQ3_WANTS=/etc/systemd/system/multi-user.target.wants/mag-llm-iq3.service
fail() { echo "both-sockets: $*" >&2; exit 1; }
[[ $EUID -eq 0 ]] || fail "root required"
[[ -f $ENV_SRC ]] || fail "missing $ENV_SRC"

export MAG_LLM_ENV=$ENV_SRC
unset ENGINE IK_BIN NUMA_ARGS LLAMA_NUMA THREADS THREADS_BATCH BATCH UBATCH \
  IK_RTR IK_ENV IK_ARGS IK_CRS IK_SLOT_SAVE IK_SLOT_SAVE_PATH LOAD_ARGS LLAMA_BIN EXTRA_ARGS
# shellcheck disable=SC1091
source "$ROOT/ops/deepseek/llm-config.sh"
tmp=$(mktemp)
trap 'rm -f "$tmp"' EXIT
render_unit "$tmp"
for token in \
  '/usr/bin/numactl --interleave=all ' \
  '--numa distribute ' \
  '--threads 28 ' \
  '--threads-batch 28 ' \
  '--batch-size 2048 ' \
  '--ubatch-size 2048 '
do
  grep -Fq -- "$token" "$tmp" || fail "rendered unit missing: $token"
done
grep -q 'CPUAffinity=\|NUMAPolicy=\|AllowedCPUs=' "$tmp" && fail "rendered unit still pins a node" || true

if systemctl cat mag-llm-iq3.service >/dev/null 2>&1; then
  systemctl disable --now mag-llm-iq3.service >/dev/null 2>&1 || systemctl stop mag-llm-iq3.service >/dev/null 2>&1 || true
  rm -f "$IQ3_WANTS"
fi
install -d -m 0750 /etc/mag-llm
install -m 0640 -o root -g root "$ENV_SRC" "$ENV"
install -m 0644 "$tmp" "$UNIT"
rm -f "$GUARD"
systemctl daemon-reload
effective=$(systemctl show -p ExecStart --value mag-llm.service)
[[ "$effective" == *'--interleave=all'* && "$effective" == *'--threads 28'* && "$effective" == *'--ubatch-size 2048'* ]] \
  || fail "systemd ExecStart is not the both-socket unit"
systemctl show -p CPUAffinity -p NUMAPolicy -p AllowedCPUs --value mag-llm.service | grep -q '[0-9]' \
  && fail "systemd still binds mag-llm to a CPU set" || true
echo "both-sockets: unit installed, restarting mag-llm"
exec bash "$ROOT/ops/deepseek/warm.sh" restart-llm
