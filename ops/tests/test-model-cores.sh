#!/usr/bin/env bash
# mag-test-timeout: 180
if [ -z "${MAG_TEST_DEADLINE_ARMED:-}" ]; then
  export MAG_TEST_DEADLINE_ARMED=1
  exec timeout --kill-after=5s 180s bash "$0" "$@"
fi
# Builds, deploys and bot VMs stay off the model cores 0-27.
set -euo pipefail
cd "$(dirname "$0")/../.."
for f in ops/update.sh ops/deploy.sh ops/coding/clean-deploy.sh ops/coding/build-runtime.sh ops/qwen/build-runtime.sh; do
  grep -Fq 'exec taskset -c "${MAG_BUILD_CPUS:-28-55}" nice -n 19 ionice -c3 bash "$0" "$@"' "$f" || { echo "FAIL: $f not pinned"; exit 1; }
done
grep -Fq 'ops/vm/pin-vcpus.sh' ops/deploy.sh
grep -Fq '"emulatorpin", domain(n), cpus' mag-room/native/vm.cpp
grep -Fq 'vcpupin "$id" "$v" "$cpus" --config' ops/vm/pin-vcpus.sh
out=$(MAG_CORE_SAMPLE_S=1 bash ops/check-model-cores.sh 2>&1)
grep -Eq '^model-cores: (OK|WARN)' <<<"$out"
echo "model-cores PASS"
