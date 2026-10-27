#!/usr/bin/env bash
# The parallel merge-gate runner must fail on the first error and kill the
# rest, so a 15s sleeper does not keep the gate open.
# mag-test-timeout: 180
if [ -z "${MAG_TEST_DEADLINE_ARMED:-}" ]; then
  export MAG_TEST_DEADLINE_ARMED=1
  exec timeout --kill-after=5s 180s bash "$0" "$@"
fi
set -eu
root=$(cd -- "$(dirname -- "$0")/../../.." && pwd)
cd "$root"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
export MAG_CI_JOBS=2

bash ops/ci/run-parallel.sh >"$tmp/ok" <<'EOF'
alpha :: true
beta :: printf 'TEST 0.01s demo\n'
EOF
ok_st=$?
[[ $ok_st -eq 0 ]]
grep -q 'STEP ' "$tmp/ok"
grep -q 'TEST 0.01s demo' "$tmp/ok"
grep -q 'PARALLEL wall ' "$tmp/ok"

start=$(date +%s)
set +e
bash ops/ci/run-parallel.sh >"$tmp/bad" <<'EOF'
bad :: bash -c 'echo no; exit 3'
slow :: sleep 15
EOF
bad_st=$?
set -e
end=$(date +%s)
[[ $bad_st -eq 3 ]]
[[ $((end - start)) -lt 8 ]]
grep -q 'FAIL ' "$tmp/bad"

cat >"$tmp/slow_test.py" <<'PY'
import time
import unittest

class S(unittest.TestCase):
    def test_hangs(self):
        time.sleep(30)
PY
start=$(date +%s)
set +e
python3 ops/ci/run-native-suite.py --file "$tmp/slow_test.py" --per-test 1 >"$tmp/alarm" 2>"$tmp/alarm.err"
alarm_st=$?
set -e
end=$(date +%s)
[[ $alarm_st -ne 0 ]]
[[ $((end - start)) -lt 5 ]]
grep -q 'per-test timeout' "$tmp/alarm" "$tmp/alarm.err"

grep -q 'mag_ci_worktree_magroom_pids' ops/ci/reap.sh
grep -q 'FAIL leftover magroom after merge gate' ops/ci/native-gate.sh
grep -q '_kill_worktree_magroom' ops/ci/mag_deadline.py
if grep -R -n 'pgrep -f.*run_ci_force' --include='*.py' --include='*.sh' \
  --exclude='test-gate-parallel.sh' ops mag-room .github >/dev/null 2>&1; then
  echo 'queued-run wait still matches its own command line' >&2
  exit 1
fi

# Leftover magroom is identified by this worktree's executable, not argv.
# /bin/sleep copied to that path keeps exe == MAG_CI_MAGROOM.
fake=$tmp/magroom
cp /bin/sleep "$fake"
chmod +x "$fake"
"$fake" 3600 &
leak=$!
for _ in $(seq 1 50); do
  kill -0 "$leak" 2>/dev/null && break
  sleep 0.05
done
kill -0 "$leak"
echo 'ok :: true' >"$tmp/list"
export MAG_CI_MAGROOM=$fake
set +e
MAG_CI_GATE_LIST=$tmp/list MAG_CI_JOBS=1 bash ops/ci/native-gate.sh --parallel >"$tmp/before" 2>"$tmp/before.err"
before_st=$?
set -e
[[ $before_st -eq 0 ]]
grep -q 'killing leftover magroom from this worktree' "$tmp/before.err"
if kill -0 "$leak" 2>/dev/null; then
  echo 'before-reap left magroom running' >&2
  kill -KILL "$leak" 2>/dev/null || true
  exit 1
fi
wait "$leak" 2>/dev/null || true

"$fake" 3600 &
leak=$!
for _ in $(seq 1 50); do
  kill -0 "$leak" 2>/dev/null && break
  sleep 0.05
done
kill -0 "$leak"
set +e
MAG_CI_SKIP_BEFORE_REAP=1 MAG_CI_GATE_LIST=$tmp/list MAG_CI_JOBS=1 \
  bash ops/ci/native-gate.sh --parallel >"$tmp/after" 2>"$tmp/after.err"
after_st=$?
set -e
[[ $after_st -ne 0 ]]
grep -q 'FAIL leftover magroom after merge gate' "$tmp/after.err"
if kill -0 "$leak" 2>/dev/null; then
  echo 'after-reap left magroom running' >&2
  kill -KILL "$leak" 2>/dev/null || true
  exit 1
fi
wait "$leak" 2>/dev/null || true

"$fake" 3600 &
sibling=$!
for _ in $(seq 1 50); do
  kill -0 "$sibling" 2>/dev/null && break
  sleep 0.05
done
kill -0 "$sibling"
PYTHONPATH=$root/ops/ci MAG_CI_MAGROOM=$fake python3 - <<'PY'
import mag_deadline, os, subprocess, time
mag_deadline.arm(30)
child = subprocess.Popen([os.environ['MAG_CI_MAGROOM'], '3600'])
for _ in range(50):
    if child.poll() is None:
        break
    time.sleep(0.05)
assert child.poll() is None, 'tracked magroom did not start'
mag_deadline._kill_tracked()
for _ in range(50):
    if child.poll() is not None:
        break
    time.sleep(0.05)
assert child.poll() is not None, 'mag_deadline left its own magroom running'
print('own-dead')
PY
if ! kill -0 "$sibling" 2>/dev/null; then
  echo 'mag_deadline killed a sibling worktree magroom' >&2
  exit 1
fi
# Worktree-wide kill is for the gate after-check, not atexit.
PYTHONPATH=$root/ops/ci MAG_CI_MAGROOM=$fake python3 -c 'import mag_deadline; mag_deadline._kill_worktree_magroom()'
if kill -0 "$sibling" 2>/dev/null; then
  echo 'worktree kill left sibling magroom running' >&2
  kill -KILL "$sibling" 2>/dev/null || true
  exit 1
fi
wait "$sibling" 2>/dev/null || true

echo 'ok :: true' >"$tmp/list"
MAG_CI_GATE_LIST=$tmp/list MAG_CI_JOBS=1 bash ops/ci/native-gate.sh --parallel >"$tmp/tiny" 2>&1
grep -q 'MERGE GATE wall ' "$tmp/tiny"

# Incremental cache, parallel contracts, abort on first failure.
if grep -E 'rm[[:space:]]+-r' ops/ci/native-gate.sh ops/ci/incremental-build.sh ops/ci/local-ci.sh; then
  echo 'incremental build must not delete build/' >&2
  exit 1
fi
grep -q 'ops/ci/incremental-build.sh' ops/ci/native-gate.sh
grep -q 'native-fast.list' ops/ci/native-gate.sh
grep -q 'MERGE GATE wall' ops/ci/native-gate.sh
# shellcheck disable=SC2016 # literal local-ci assignment, not a shell expansion
grep -F 'JOBS=${MAG_CI_JOBS:-$(nproc' ops/ci/local-ci.sh >/dev/null
if grep -q 'JOBS=4' ops/ci/local-ci.sh; then
  echo 'local-ci still hardcodes JOBS=4' >&2
  exit 1
fi
grep -q -- '--prepare' ops/ci/native-gate.sh
build_before=$(readlink -f "$root/build" 2>/dev/null || true)
printed=$(MAG_CI_CACHE="$tmp/cache" bash ops/ci/incremental-build.sh --print-cache)
printf '%s\n' "$printed" | grep -q "MAG_CI_CACHE=$tmp/cache"
printf '%s\n' "$printed" | grep -q "CCACHE_DIR=$tmp/cache/ccache"
printf '%s\n' "$printed" | grep -q "TREE=$tmp/cache/trees/"
[[ -d $tmp/cache/ccache ]]
[[ -d $(printf '%s\n' "$printed" | sed -n 's/^TREE=//p') ]]
# The old shared $CACHE/build must not be the ninja tree.
if [[ -d $tmp/cache/build ]]; then
  echo 'print-cache still created the shared build/ leftover' >&2
  exit 1
fi
build_after=$(readlink -f "$root/build" 2>/dev/null || true)
if [[ $build_before != "$build_after" ]]; then
  echo '--print-cache retargeted ./build' >&2
  exit 1
fi
inside=$(MAG_CI_CACHE="$root/work/cache" HOME="$tmp/home" bash ops/ci/incremental-build.sh --print-cache)
if printf '%s\n' "$inside" | grep -q "MAG_CI_CACHE=$root/work/cache"; then
  echo 'cache was placed inside the wiped work/ tree' >&2
  exit 1
fi
printf '%s\n' "$inside" | grep -E -q 'MAG_CI_CACHE=.*/.cache/mag-ci|MAG_CI_CACHE=/var/tmp/mag-ci-'
# Mag runner HOME is often /work; never put the cache there.
wiped=$(MAG_CI_CACHE='' CCACHE_DIR=/work/.cache/ccache HOME=/work \
  bash ops/ci/incremental-build.sh --print-cache)
printf '%s\n' "$wiped" | grep -q "MAG_CI_CACHE=/var/tmp/mag-ci-$(id -u)"
printf '%s\n' "$wiped" | grep -q "CCACHE_DIR=/var/tmp/mag-ci-$(id -u)/ccache"

# Mag creates ~/ci-local/wt/prN-<sha4> per PR. Two paths must both build
# against one MAG_CI_CACHE; a leftover CMAKE_HOME_DIRECTORY from the first
# must not abort the second.
install_wt() {
  local d=$1
  mkdir -p "$d/ops/ci"
  cp "$root/ops/ci/incremental-build.sh" "$d/ops/ci/incremental-build.sh"
  cat >"$d/CMakeLists.txt" <<'EOF'
cmake_minimum_required(VERSION 3.16)
project(mag_ci_wt LANGUAGES CXX)
add_executable(magroom main.cpp)
EOF
  printf '%s\n' 'int main(){return 0;}' >"$d/main.cpp"
}
wt1=$tmp/wt/pr163-72e0
wt2=$tmp/wt/pr154-3f89
install_wt "$wt1"
install_wt "$wt2"
export MAG_CI_CACHE="$tmp/shared"
# Old shared leftover from 72e02b8: must not be reused as this worktree's tree.
mkdir -p "$tmp/shared/build"
printf 'CMAKE_HOME_DIRECTORY:INTERNAL=%s\n' "$wt1" >"$tmp/shared/build/CMakeCache.txt"
bash "$wt1/ops/ci/incremental-build.sh"
bash "$wt2/ops/ci/incremental-build.sh"
[[ -x $wt1/build/magroom && -x $wt2/build/magroom ]]
tree1=$(readlink -f "$wt1/build")
tree2=$(readlink -f "$wt2/build")
[[ $tree1 != "$tree2" ]]
[[ $tree1 != "$tmp/shared/build" && $tree2 != "$tmp/shared/build" ]]
grep -Fq "CMAKE_HOME_DIRECTORY:INTERNAL=$wt1" "$tree1/CMakeCache.txt"
grep -Fq "CMAKE_HOME_DIRECTORY:INTERNAL=$wt2" "$tree2/CMakeCache.txt"
# Foreign cache in this worktree's tree is reset; objects stay.
printf 'CMAKE_HOME_DIRECTORY:INTERNAL=%s\n' "$wt1" >"$tree2/CMakeCache.txt"
printf 'keep\n' >"$tree2/keep.o"
mkdir -p "$tree2/CMakeFiles"
printf 'stale\n' >"$tree2/CMakeFiles/stale"
bash "$wt2/ops/ci/incremental-build.sh" --prepare
[[ -f $tree2/keep.o ]]
[[ ! -f $tree2/CMakeCache.txt ]]
bash "$wt2/ops/ci/incremental-build.sh"
[[ -x $wt2/build/magroom ]]
grep -Fq "CMAKE_HOME_DIRECTORY:INTERNAL=$wt2" "$tree2/CMakeCache.txt"
[[ -f $tree2/keep.o ]]
echo 'gate parallel PASS'
