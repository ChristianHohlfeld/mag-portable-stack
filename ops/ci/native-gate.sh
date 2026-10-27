#!/usr/bin/env bash
# Single-machine native merge gate.
#
# Shards the room suite into contiguous slices (one magroom per shard, tests
# inside a shard keep their order) and runs those shards beside the binary
# and companion lists. Fast contracts overlap the incremental ninja so a
# warm ./build and ccache do not sit in front of the pool. GitHub keeps the
# same lists as separate jobs; this script is what local CI and the
# disposable VM run, because one machine does not get GitHub's job-level
# parallelism.
#
# Target: a verdict under 120s of wall clock on mag (14 cores, CPUs 42-55)
# when ccache and the persistent ninja tree (~/.cache/mag-ci) are warm.
# work/ is wiped every local run; the cache is never inside it. This script
# never deletes build/ or the cache. The first non-zero status aborts the
# rest of that pool. Always prints MERGE GATE wall.
#
# Room shards alarm each test at 30s. The @N on a shard is only the process
# cap for many of those tests. Untagged commands use --timeout 180, the cap
# the fast list already had, because those scripts bundle many checks.
#
# Leftover magroom from this worktree is killed before the pool and again
# after. A leftover after the pool fails the gate. Production magroom is
# never touched.
set -euo pipefail
ROOT=$(cd -- "$(dirname -- "$(readlink -f "$0")")/../.." && pwd)
cd "$ROOT"
# shellcheck disable=SC1091 # reap.sh is passed to shellcheck on its own
source "$ROOT/ops/ci/reap.sh"
jobs=${MAG_CI_JOBS:-$(nproc)}

if [[ ${1:-} != --parallel ]]; then
  printf 'native-gate: usage: %s --parallel\n' "$0" >&2
  exit 64
fi

if [[ ${MAG_CI_SKIP_BEFORE_REAP:-} != 1 ]]; then
  mag_ci_kill_worktree_magroom || true
fi
# Point ./build at ~/.cache/mag-ci (or MAG_CI_CACHE) before the pending check
# so a wiped worktree still sees a warm ninja tree and stays one pool.
bash "$ROOT/ops/ci/incremental-build.sh" --prepare

list=$(mktemp)
phase2=$(mktemp)
trap 'rm -f "$list" "$phase2"' EXIT

run_pool() {
  set +e
  bash ops/ci/run-parallel.sh --jobs "$jobs" --timeout 180 <"$1"
  local st=$?
  set -e
  return "$st"
}

chmod_bins() {
  chmod +x build/test-* build/magroom build/mag-audit-gateway build/mag-numa-swap build/s23ctl
}

# 0 = ninja has work (or no tree). 1 = warm, no compile.
build_pending() {
  [[ -x build/magroom && -f build/CMakeCache.txt ]] || return 0
  cmake --build build -- -n 2>/dev/null | grep -Fq 'no work to do' && return 1
  return 0
}

emit_after_build() {
  python3 ops/ci/run-native-suite.py --emit-shards --jobs "$jobs" --file mag-room/tests/test_native.py
  cat ops/ci/native-bins.list ops/ci/native-rest.list
}

start=$(date +%s%N)
st=0
if [[ -n ${MAG_CI_GATE_LIST:-} ]]; then
  cat "$MAG_CI_GATE_LIST" >"$list"
  run_pool "$list" || st=$?
elif build_pending; then
  {
    echo '@180 incremental build :: bash ops/ci/incremental-build.sh'
    cat ops/ci/native-fast.list
  } >"$list"
  if run_pool "$list"; then
    chmod_bins
    emit_after_build >"$phase2"
    run_pool "$phase2" || st=$?
  else
    st=$?
  fi
else
  chmod_bins
  {
    cat ops/ci/native-fast.list
    emit_after_build
  } >"$list"
  run_pool "$list" || st=$?
fi
end=$(date +%s%N)
cores=$(nproc)
python3 -c 'import sys; print("MERGE GATE wall %.2fs cores=%s jobs=%s fail=%s" % ((int(sys.argv[2]) - int(sys.argv[1])) / 1e9, sys.argv[3], sys.argv[4], sys.argv[5]))' \
  "$start" "$end" "$cores" "$jobs" "$([[ $st -eq 0 ]] && echo 0 || echo 1)"
if ! mag_ci_kill_worktree_magroom; then
  echo "FAIL leftover magroom after merge gate" >&2
  exit 1
fi
exit "$st"
