#!/usr/bin/env bash
# Run independent commands with a per-command hard timeout.
# stdin lines: [@SECONDS] name :: command
#   --jobs N     concurrent commands (default: nproc)
#   --timeout S  default per-command timeout in seconds (default: 240)
# Exit 0 only when every command exits 0. A timeout exits non-zero.
# Each command runs in its own session. This process stays in the caller's
# process group and kills every descendant session on exit, timeout, or abort.
set -uo pipefail
ROOT=$(cd -- "$(dirname -- "$(readlink -f "$0")")/../.." && pwd)
# shellcheck disable=SC1091 # reap.sh is passed to shellcheck on its own in native-fast.list
source "$ROOT/ops/ci/reap.sh"
mag_ci_arm "$ROOT/ops/ci/ci_reap.py" "$@"
REAP=$ROOT/ops/ci/ci_reap.py
jobs=$(nproc 2>/dev/null || echo 1)
timeout_s=240
while [[ $# -gt 0 ]]; do
  case $1 in
    --jobs) jobs=${2:?}; shift 2 ;;
    --timeout) timeout_s=${2:?}; shift 2 ;;
    *) echo "usage: $0 [--jobs N] [--timeout SEC]" >&2; exit 64 ;;
  esac
done
[[ $jobs =~ ^[1-9][0-9]*$ ]] || jobs=1
[[ $timeout_s =~ ^[1-9][0-9]*$ ]] || { echo "bad timeout: $timeout_s" >&2; exit 64; }
logdir=$(mktemp -d /tmp/mag-parallel.XXXXXX)
echo "parallel logs: $logdir"
start_wall=$(date +%s.%N)
running=0
fail=0
n=0
declare -a pids=()
reap() {
  local st=0 found=0 i pid
  wait -n || st=$?
  if [[ $st -eq 127 ]]; then
    running=0
    pids=()
    return 0
  fi
  for i in "${!pids[@]}"; do
    pid=${pids[$i]}
    if ! kill -0 "$pid" 2>/dev/null; then
      unset 'pids[i]'
      found=1
      break
    fi
  done
  if [[ $found -eq 1 ]]; then
    running=$((running - 1))
    [[ $st -eq 0 ]] || fail=1
  fi
  if ((${#pids[@]})); then
    python3 "$REAP" --kill "$$" "${pids[@]}" || true
  else
    python3 "$REAP" --kill "$$" || true
  fi
}
while IFS= read -r line || [[ -n $line ]]; do
  [[ -z ${line//[[:space:]]/} ]] && continue
  [[ $line == \#* ]] && continue
  limit=$timeout_s
  if [[ $line == @* ]]; then
    limit=${line%%[[:space:]]*}
    limit=${limit#@}
    line=${line#*[[:space:]]}
    [[ $limit =~ ^[1-9][0-9]*$ ]] || { echo "bad per-command timeout on: $line" >&2; exit 64; }
  fi
  [[ $line == *" :: "* ]] || { echo "expected 'name :: command': $line" >&2; exit 64; }
  name=${line%% :: *}
  cmd=${line#* :: }
  while [[ $running -ge $jobs ]]; do reap; done
  n=$((n + 1))
  safe=$(printf '%s' "$name" | tr -c 'A-Za-z0-9._-' '_')
  out="$logdir/${n}-${safe}.log"
  (
    start=$(date +%s.%N)
    timeout --kill-after=5s "${limit}s" python3 "$REAP" --supervise -- bash -c "$cmd" >"$out" 2>&1
    st=$?
    end=$(date +%s.%N)
    dur=$(awk -v s="$start" -v e="$end" 'BEGIN{printf "%.2f", e-s}')
    if [[ $st -eq 0 ]]; then
      printf 'PASS %7ss  %s\n' "$dur" "$name"
    else
      printf 'FAIL %7ss  %s (exit %s, log %s)\n' "$dur" "$name" "$st" "$out"
      tail -n 20 "$out" | sed 's/^/    /'
    fi
    exit "$st"
  ) &
  pids+=("$!")
  running=$((running + 1))
done
while [[ $running -gt 0 ]]; do reap; done
end_wall=$(date +%s.%N)
awk -v s="$start_wall" -v e="$end_wall" -v f="$fail" 'BEGIN{printf "parallel wall %.2fs  fail=%s\n", e-s, f}'
exit "$fail"
