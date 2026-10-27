#!/usr/bin/env bash
# Run independent commands with a per-command hard timeout.
# stdin lines: [@SECONDS] name :: command
#   --jobs N     concurrent commands (default: nproc)
#   --timeout S  default per-command timeout in seconds (default: 240)
# Exit 0 only when every command exits 0. A timeout exits 124.
set -uo pipefail
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
reap() {
  local st=0
  wait -n || st=$?
  running=$((running - 1))
  if [[ $st -ne 0 ]]; then fail=1; fi
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
    timeout --kill-after=5s "${limit}s" bash -c "$cmd" >"$out" 2>&1
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
  running=$((running + 1))
done
while [[ $running -gt 0 ]]; do reap; done
end_wall=$(date +%s.%N)
awk -v s="$start_wall" -v e="$end_wall" -v f="$fail" 'BEGIN{printf "parallel wall %.2fs  fail=%s\n", e-s, f}'
exit "$fail"
