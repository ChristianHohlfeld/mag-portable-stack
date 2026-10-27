#!/usr/bin/env bash
# mag-test-timeout: 180
if [ -z "${MAG_TEST_DEADLINE_ARMED:-}" ]; then
  export MAG_TEST_DEADLINE_ARMED=1
  exec timeout --kill-after=5s 180s bash "$0" "$@"
fi
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf -- "$tmp"' EXIT
cat >"$tmp/node" <<'EOF'
Node 0 MemTotal: 260000000 kB
Node 0 MemFree: 10000000 kB
Node 0 FilePages: 105000000 kB
Node 0 Shmem: 500000 kB
Node 0 Dirty: 50000 kB
Node 0 Writeback: 50000 kB
Node 0 SReclaimable: 2000000 kB
EOF
printf 'MemAvailable: 140000000 kB\n' >"$tmp/global"
export MAG_NUMA_NODE0_MEMINFO="$tmp/node" MAG_NUMA_GLOBAL_MEMINFO="$tmp/global"
n="$(bash "$here/numa-node0-available.sh")"
((n>90000000)) || { echo "Clean page cache not reclaimed: $n" >&2; exit 1; }
printf 'MemAvailable: 15000000 kB\n' >"$tmp/global"
n="$(bash "$here/numa-node0-available.sh")"
((n==15000000)) || exit 1
printf 'MemAvailable: 0 kB\n' >"$tmp/global"
if bash "$here/numa-node0-available.sh" >/dev/null;then exit 1;fi
grep -Fq 'node0_available + local_anon' "$here/numa-isolation.sh"
echo 'NUMA node0 MemAvailable/page-cache/Q4 gate PASS'
