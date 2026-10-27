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

grep -q 'worktree_magroom_pids' ops/ci/native-gate.sh
grep -q 'FAIL leftover magroom after merge gate' ops/ci/native-gate.sh
echo 'ok :: true' >"$tmp/list"
MAG_CI_GATE_LIST=$tmp/list MAG_CI_JOBS=1 bash ops/ci/native-gate.sh --parallel >"$tmp/tiny" 2>&1
grep -q 'MERGE GATE wall ' "$tmp/tiny"
echo 'gate parallel PASS'
