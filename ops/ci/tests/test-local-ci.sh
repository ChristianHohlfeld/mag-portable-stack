#!/usr/bin/env bash
# mag-test-timeout: 60
if [ -z "${MAG_TEST_DEADLINE_ARMED:-}" ]; then
  export MAG_TEST_DEADLINE_ARMED=1
  exec timeout --kill-after=5s 60s bash "$0" "$@"
fi
# Fail-fast, NUMA1 pin, backup-e2e skip unless backup/ changed.
set -euo pipefail
root=$(cd -- "$(dirname "$0")/../../.." && pwd)
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

grep -Fq 'taskset -c 42-55' "$root/ops/ci/local-ci.sh"
grep -Fq 'wait-native-build.sh' "$root/ops/ci/local-ci.sh"
grep -Fq 'backup-affected.sh' "$root/ops/ci/local-ci.sh"

# Pin is skipped when MAG_CI_NO_PIN=1 or the SMT siblings are absent.
MAG_CI_NO_PIN=1 MAG_CI_REAP_PROBE='true' bash "$root/ops/ci/local-ci.sh"

# Generated job list overlaps Build with independent work and waits for binaries.
MAG_CI_NO_PIN=1 MAG_CI_LIST_ONLY=1 MAG_CI_FORCE_BACKUP=1 bash "$root/ops/ci/local-ci.sh" --no-browser \
  >"$tmp/full.list" 2>"$tmp/full.err"
grep -Fq 'Build ::' "$tmp/full.list"
grep -Fq 'Independent fast contracts' "$root/ops/ci/native-fast.list" || true
grep -Fq 'backup restore e2e' "$tmp/full.list"
if ! grep -Fq 'Vendor pinned Moonshine' "$tmp/full.list"; then
  grep -Fq 'Vendor pinned Moonshine' "$tmp/full.err"
fi
if ! grep -Fq 'Empty legacy Gitea directory' "$tmp/full.list"; then
  grep -Fq 'Empty legacy Gitea directory' "$tmp/full.err"
fi
grep -Fq 'wait-native-build.sh' "$tmp/full.list"
grep -Fq 'room-clear ::' "$tmp/full.list"

# No backup/ path in MAG_AFFECTED_FILES skips the restore e2e. Do not use
# this branch's git diff: a 300s deadline edit in ops/backup/tests/ is a
# backup/ change and would otherwise force the 145s restore.
MAG_CI_NO_PIN=1 MAG_CI_LIST_ONLY=1 MAG_CI_FORCE_BACKUP=0 \
  MAG_AFFECTED_FILES=$'ops/deploy.sh\nmag-room/native/magroom.cpp' \
  bash "$root/ops/ci/local-ci.sh" --no-browser \
  >"$tmp/skip.list" 2>"$tmp/skip.err"
if grep -Fq 'backup restore e2e' "$tmp/skip.list"; then
  echo 'backup e2e should be skipped without backup/ changes' >&2
  exit 1
fi
grep -Fq 'backup restore e2e' "$tmp/skip.err"

# backup-affected: only backup/ paths, not deploy.sh.
if MAG_AFFECTED_FILES=$'ops/deploy.sh\nmag-room/native/magroom.cpp' \
  bash "$root/ops/ci/backup-affected.sh"; then
  echo 'deploy.sh must not force backup e2e' >&2
  exit 1
fi
MAG_AFFECTED_FILES='ops/backup/backup.sh' bash "$root/ops/ci/backup-affected.sh"
MAG_AFFECTED_FILES='backup/notes.txt' bash "$root/ops/ci/backup-affected.sh"
MAG_CI_FORCE_BACKUP=1 MAG_AFFECTED_FILES='mag-room/native/magroom.cpp' \
  bash "$root/ops/ci/backup-affected.sh"

# First failure kills the rest (same contract as run-parallel).
cat >"$tmp/fail.list" <<'EOF'
bad :: bash -c 'echo no; exit 3'
slow :: sleep 15
EOF
start=$(date +%s)
set +e
MAG_CI_NO_PIN=1 MAG_CI_LOCAL_LIST="$tmp/fail.list" bash "$root/ops/ci/local-ci.sh" --no-browser \
  >"$tmp/fail.out" 2>"$tmp/fail.err"
st=$?
set -e
end=$(date +%s)
[[ $st -eq 3 ]]
[[ $((end - start)) -lt 8 ]]
grep -q 'local CI FAIL' "$tmp/fail.out" "$tmp/fail.err"

echo 'local-ci parallel PASS'
