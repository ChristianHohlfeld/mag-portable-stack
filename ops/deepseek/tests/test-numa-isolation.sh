#!/usr/bin/env bash
# mag-test-timeout: 180
if [ -z "${MAG_TEST_DEADLINE_ARMED:-}" ]; then
  export MAG_TEST_DEADLINE_ARMED=1
  exec timeout --kill-after=5s 180s bash "$0" "$@"
fi
# Offline NUMA safety contract; no systemd/host mutation.
set -euo pipefail
cd "$(dirname "$0")/../../.."
bash -n ops/deepseek/numa-isolation.sh ops/deepseek/numa-bench.sh ops/deepseek/warm.sh
s=ops/deepseek/numa-isolation.sh
grep -Fq 'node0_available + local_anon >= rssanon + 12*1024*1024' "$s" ||
 { echo 'missing node0 MemAvailable + own Q4 memory capacity gate' >&2; exit 1; }
for expected in '--cpunodebind=0 --membind=0' '--numa numactl' '--threads 16' '--threads-batch 16' 'ConditionPathExists=/run/mag-model-pin-explicitly-enabled' 'ExecStartPre=/usr/bin/false' 'systemctl disable --now mag-model-pin.service' 'render_unit "$TMP/unit"' "assert cache(live)==cache(new)"; do
  grep -Fq -- "$expected" "$s" || { echo "missing safety contract: $expected" >&2; exit 1; }
done
! grep -Eq '^Conflicts=mag-llm-iq3.service' "$s" || { echo "systemd conflict could stop Q4" >&2; exit 1; }
! grep -Eq 'systemctl (restart|start|kill).*mag-llm.service' "$s"
grep -Fq -- '--port 8092' ops/deepseek/mag-llm-secondary.service.in
grep -Fq 'CPUAffinity=14-27 42-55' ops/deepseek/mag-llm-secondary.service.in
# Synthetic benchmark comparison: speed regression must fail.
t=$(mktemp -d); trap 'rm -rf "$t"' EXIT
cat > "$t/before" <<'EOF'
| tg128 | 40 | 11 | 128 | 8.32 | 2 | 16 |
| pp2048 | 2048 | 44 | 16 | 7 | 45 | 46 |
EOF
cat > "$t/after" <<'EOF'
| tg128 | 40 | 12 | 128 | 7.50 | 2 | 18 |
| pp2048 | 2048 | 47 | 16 | 7 | 42 | 44 |
EOF
if bash ops/deepseek/numa-bench.sh compare "$t/before" "$t/after" >/dev/null 2>&1;then
  echo "slower model passed speed gate" >&2; exit 1
fi
sed -i 's/7.50/8.50/' "$t/after"
bash ops/deepseek/numa-bench.sh compare "$t/before" "$t/after" >/dev/null

# Exercise real ik renderer: node0 bind, native NUMA, threads, cache persistence.
help_flags="--model --alias --host --port --ctx-size --parallel --threads --threads-batch --numa --batch-size --ubatch-size --mlock --flash-attn --cache-type-k --cache-type-v --cache-ram --jinja --chat-template-file --reasoning-format --temp --top-p --min-p --metrics --webui -rtr --slot-save-path -crs"
for f in $help_flags; do echo "  $f VALUE"; done > "$t/help"
printf '#!/bin/sh\nexit 0\n' > "$t/llama-server"; chmod +x "$t/llama-server"
env HELP_FILE="$t/help" IK_BIN="$t/llama-server" THREADS=16 THREADS_BATCH=16 BATCH=1024 UBATCH=1024 \
    NUMA_ARGS="--cpunodebind=0 --membind=0" LLAMA_NUMA=numactl IK_RTR=1 \
    IK_SLOT_SAVE=1 IK_SLOT_SAVE_PATH=/var/lib/mag-llm/slots IK_CRS=0.20 \
    bash ops/deepseek/render-ik-unit.sh ops/mag-llm.service "$t/unit" >/dev/null
for expected in '--cpunodebind=0 --membind=0' '--numa numactl' '--threads 16' '--threads-batch 16' '--cache-ram 16384' '--slot-save-path /var/lib/mag-llm/slots' '-crs 0.20' '-rtr'; do
  grep -Fq -- "$expected" "$t/unit" || { echo "render regression: $expected" >&2; exit 1; }
done
# Deliberately unknown flag => render must fail without leaving a unit behind.
if env HELP_FILE="$t/help" IK_BIN="$t/llama-server" THREADS=16 THREADS_BATCH=16 BATCH=1024 UBATCH=1024 \
    NUMA_ARGS="--cpunodebind=0 --membind=0" LLAMA_NUMA=numactl IK_ARGS=-invented \
    bash ops/deepseek/render-ik-unit.sh ops/mag-llm.service "$t/bad" >/dev/null 2>&1; then
  echo "unsafe ik flag accepted" >&2; exit 1
fi
[[ ! -e "$t/bad" ]] || { echo "render failed but wrote a unit" >&2; exit 1; }

echo "NUMA safety + bench regression tests passed"
