#!/usr/bin/env bash
# Single-machine native merge gate.
#
# Shards the room suite into contiguous slices (one magroom per shard, tests
# inside a shard keep their order) and runs those shards beside the fast,
# binary and companion lists. GitHub keeps the same lists as separate jobs;
# the room job shards test_native the same way. This script is what local CI
# and the disposable VM run, because one machine does not get GitHub's
# job-level parallelism.
#
# Target: a verdict under 120s of wall clock on mag (14 cores, CPUs 42-55)
# when ccache and ./build are already warm. This script never deletes build/.
#
# Room shards alarm each test at 30s. The @N on a shard is only the process
# cap for many of those tests. Untagged commands use --timeout 180, the cap
# the fast list already had, because those scripts bundle many checks.
set -euo pipefail
ROOT=$(cd -- "$(dirname -- "$(readlink -f "$0")")/../.." && pwd)
cd "$ROOT"
jobs=${MAG_CI_JOBS:-$(nproc)}

if [[ ${1:-} != --parallel ]]; then
  printf 'native-gate: usage: %s --parallel\n' "$0" >&2
  exit 64
fi

magroom=${MAG_CI_MAGROOM:-$ROOT/build/magroom}
worktree_magroom_pids() {
  local pid exe
  for pid in /proc/[0-9]*; do
    pid=${pid#/proc/}
    exe=$(readlink -f "/proc/$pid/exe" 2>/dev/null || true)
    if [[ "$exe" == "$magroom" ]]; then
      printf '%s\n' "$pid"
    fi
  done
}

# Only this worktree's magroom. Production /opt/mag-stack/.../magroom is left alone.
mapfile -t leftover < <(worktree_magroom_pids)
if ((${#leftover[@]})); then
  echo "native-gate: killing leftover magroom from this worktree: ${leftover[*]}" >&2
  kill -KILL "${leftover[@]}" 2>/dev/null || true
fi

list=$(mktemp)
trap 'rm -f "$list"' EXIT
if [[ -n ${MAG_CI_GATE_LIST:-} ]]; then
  cat "$MAG_CI_GATE_LIST" >"$list"
else
  {
    python3 ops/ci/run-native-suite.py --emit-shards --jobs "$jobs" --file mag-room/tests/test_native.py
    cat ops/ci/native-fast.list ops/ci/native-bins.list ops/ci/native-rest.list
  } >"$list"
fi

start=$(date +%s%N)
set +e
bash ops/ci/run-parallel.sh --jobs "$jobs" --timeout 180 <"$list"
st=$?
set -e
end=$(date +%s%N)
cores=$(nproc)
python3 -c 'import sys; print("MERGE GATE wall %.2fs cores=%s jobs=%s fail=%s" % ((int(sys.argv[2]) - int(sys.argv[1])) / 1e9, sys.argv[3], sys.argv[4], sys.argv[5]))' \
  "$start" "$end" "$cores" "$jobs" "$([[ $st -eq 0 ]] && echo 0 || echo 1)"
mapfile -t leftover < <(worktree_magroom_pids)
if ((${#leftover[@]})); then
  echo "FAIL leftover magroom after merge gate: ${leftover[*]}" >&2
  kill -KILL "${leftover[@]}" 2>/dev/null || true
  exit 1
fi
exit "$st"
