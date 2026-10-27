#!/usr/bin/env bash
# mag-test-timeout: 180
if [ -z "${MAG_TEST_DEADLINE_ARMED:-}" ]; then
  export MAG_TEST_DEADLINE_ARMED=1
  exec timeout --kill-after=5s 180s bash "$0" "$@"
fi
# CI: four split GGUFs, strict SHA, incomplete/changed/no-manifest failures.
set -euo pipefail
root=$(cd "$(dirname "$0")/../../.." && pwd)
tmp=$(mktemp -d);trap 'rm -rf "$tmp"' EXIT
make_files() {
  rm -f "$tmp"/*
  for n in 1 2 3 4; do
    printf -v i '%05d' "$n"
    printf 'fixture for GGUF part %s\nuntrusted data\n' "$n" > "$tmp/DeepSeek-UD-IQ3_XXS-$i-of-00004.gguf"
  done
  (cd "$tmp";sha256sum *.gguf > SHA256SUMS)
}
verify() { MAG_IQ3_DIR="$tmp" MAG_IQ3_MIN_PART_BYTES=12 bash "$root/ops/deepseek/verify-iq3.sh" >/dev/null 2>&1; }
make_files
verify || { echo "complete, valid 4-part manifest rejected";exit 1; }
printf 'damage' >> "$tmp/DeepSeek-UD-IQ3_XXS-00003-of-00004.gguf"
if verify;then echo "corrupt GGUF accepted";exit 1;fi
make_files
mv "$tmp/DeepSeek-UD-IQ3_XXS-00001-of-00004.gguf" "$tmp/a.hidden"
if verify;then echo "incomplete model accepted";exit 1;fi
make_files
touch "$tmp/DeepSeek-UD-IQ3_XXS-00001-of-00004.gguf.part"
if verify;then echo "active download accepted";exit 1;fi
make_files
printf 'extra' > "$tmp/unlisted.gguf"
if verify;then echo "extra part accepted";exit 1;fi
make_files
printf '%s\n' 'fake' > "$tmp/SHA256SUMS"
if verify;then echo "untrusted manifest accepted";exit 1;fi
echo "IQ3 SHA256 fail-closed tests passed"

# Verify the lifecycle contract without any host/systemd access.
! grep -q '^Conflicts=' "$root/ops/deepseek/mag-llm-iq3.service"
grep -Fq 'systemctl is-active --quiet mag-llm.service' "$root/ops/deepseek/start-iq3.sh"
grep -Fq 'cp -p "$IQ_GUARD" "$BACK/iq3.guard"' "$root/ops/deepseek/hotswap.sh"
grep -Fq 'rm -f "$IQ_GUARD"' "$root/ops/deepseek/hotswap.sh"
grep -Fq 'install -D -m 0644 "$BACK/iq3.guard" "$IQ_GUARD"' "$root/ops/deepseek/hotswap.sh"
grep -Fq 'CPUAffinity=0-13 28-41' "$root/ops/deepseek/mag-llm-iq3.service"
grep -Fq 'NUMAPolicy=bind' "$root/ops/deepseek/mag-llm-iq3.service"
grep -Fq -- '--cpunodebind=0 --membind=0' "$root/ops/deepseek/start-iq3.sh"
grep -Fq -- '--numa numactl' "$root/ops/deepseek/start-iq3.sh"
! grep -Eq -- '--interleave=all|--numa distribute' "$root/ops/deepseek/start-iq3.sh"
grep -Fq 'numa-isolation.sh" verify' "$root/ops/deepseek/hotswap.sh"
grep -Fq 'systemctl restart mag-audit-gateway.service' "$root/ops/deepseek/hotswap.sh"
! grep -Eq 'systemctl (start|stop|restart|kill) mag-llm[.]service' "$root/ops/deepseek/hotswap.sh"
! grep -Eq 'systemctl (start|restart) mag-llm-iq3[.]service' "$root/ops/deepseek/hotswap.sh"
echo "Sequential NUMA0 lifecycle gates passed"
