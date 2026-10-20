#!/usr/bin/env bash
# One Q4 model on both sockets. Called from ops/deploy.sh. No-op when the
# running process already uses both sockets, so later deploys do not reload it.
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

live_ok() {
  [[ ! -e $GUARD ]] || return 1
  local pid cmdline allowed pinned=0 task
  pid=$(systemctl show -p MainPID --value mag-llm.service 2>/dev/null || true)
  [[ "$pid" =~ ^[1-9][0-9]*$ && -r /proc/$pid/cmdline ]] || return 1
  cmdline=$(tr '\0' ' ' < "/proc/$pid/cmdline")
  [[ "$cmdline" == *'--interleave=all'* && "$cmdline" == *'--threads 28'* \
    && "$cmdline" == *'--ubatch-size 2048'* && "$cmdline" == *'--numa distribute'* ]] || return 1
  for task in /proc/"$pid"/task/[0-9]*; do
    [[ -r "$task/status" ]] || continue
    allowed=$(awk '/^Cpus_allowed_list:/{print $2}' "$task/status")
    [[ "$allowed" == "0-13,28-41" ]] && pinned=$((pinned + 1))
  done
  (( pinned < 15 ))
}
if live_ok; then
  echo "both-sockets: already applied"
  exit 0
fi

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
echo "both-sockets: unit installed, restarting"
exec bash "$ROOT/ops/deepseek/warm.sh" restart-llm
