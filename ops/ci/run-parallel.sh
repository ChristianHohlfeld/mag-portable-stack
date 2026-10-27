#!/usr/bin/env bash
# Run independent commands with a per-command hard timeout.
# stdin lines:
#   name :: command
#   @SECONDS name :: command
#   name @SECONDS :: command
#   --jobs N     concurrent commands (default: nproc)
#   --timeout S  default per-command timeout (default: 240)
# The first non-zero status kills the other running commands and does not
# start more. The process exits with that status.
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
fail_status=0
n=0
declare -a pids=()
declare -a durs=()
declare -A pid_name=()
declare -A pid_start=()
declare -A pid_log=()

secs_between() {
  python3 -c 'import sys; print(f"{(int(sys.argv[2])-int(sys.argv[1]))/1e9:.2f}")' "$1" "$2"
}

kill_rest() {
  local pid
  python3 "$REAP" --kill "$$" || true
  if ((${#pids[@]})); then
    for pid in "${pids[@]}"; do
      kill -TERM "$pid" 2>/dev/null || true
    done
  fi
  wait || true
  pids=()
  running=0
}

note_done() {
  local pid=$1 st=$2 name dur log
  name=${pid_name[$pid]:-unknown}
  dur=$(secs_between "${pid_start[$pid]}" "$(date +%s%N)")
  log=${pid_log[$pid]}
  durs+=("$dur $name")
  if [[ $st -eq 0 ]]; then
    grep -E '^(TEST|SKIP) ' "$log" || true
    printf 'STEP %ss %s\n' "$dur" "$name"
  else
    printf 'FAIL %ss %s exit=%s log=%s\n' "$dur" "$name" "$st" "$log"
    tail -n 60 "$log" | sed 's/^/    /'
    if [[ $fail -eq 0 ]]; then
      fail=1
      fail_status=$st
    fi
  fi
}

reap_one() {
  local st=0 pid='' i
  wait -n -p pid || st=$?
  if [[ -z $pid ]]; then
    if [[ $st -eq 127 ]]; then
      running=0
      pids=()
      return 0
    fi
    printf 'run-parallel: wait -n returned no pid (status %s)\n' "$st" >&2
    fail=1
    [[ $fail_status -ne 0 ]] || fail_status=1
    kill_rest
    return 0
  fi
  for i in "${!pids[@]}"; do
    if [[ ${pids[$i]} -eq $pid ]]; then
      unset 'pids[i]'
      break
    fi
  done
  running=$((running - 1))
  note_done "$pid" "$st"
  if [[ $fail -eq 1 ]]; then
    kill_rest
    return 0
  fi
  # A finished command may have called setsid. Kill that session, spare the
  # commands that are still running.
  if ((${#pids[@]})); then
    python3 "$REAP" --kill "$$" "${pids[@]}" || true
  else
    python3 "$REAP" --kill "$$" || true
  fi
}

while IFS= read -r line || [[ -n ${line:-} ]]; do
  [[ $fail -eq 1 ]] && break
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
  if [[ $name =~ ^(.+)[[:space:]]@([0-9]+)$ ]]; then
    name=${BASH_REMATCH[1]}
    limit=${BASH_REMATCH[2]}
  fi
  while [[ $running -ge $jobs ]]; do reap_one; done
  [[ $fail -eq 1 ]] && break
  n=$((n + 1))
  safe=$(printf '%s' "$name" | tr -c 'A-Za-z0-9._-' '_')
  out="$logdir/${n}-${safe}.log"
  timeout --kill-after=5s "${limit}s" python3 "$REAP" --supervise -- bash -c "$cmd" >"$out" 2>&1 &
  pid=$!
  pid_name[$pid]=$name
  pid_start[$pid]=$(date +%s%N)
  pid_log[$pid]=$out
  pids+=("$pid")
  running=$((running + 1))
done

while [[ $running -gt 0 ]]; do reap_one; done

end_wall=$(date +%s.%N)
echo '--- slowest ---'
if ((${#durs[@]})); then
  set +o pipefail
  printf '%s\n' "${durs[@]}" | sort -nr | head -n 20 | while IFS= read -r row; do
    printf 'SLOW %s\n' "$row"
  done
  set -o pipefail
fi
cores=$(nproc 2>/dev/null || echo 1)
awk -v s="$start_wall" -v e="$end_wall" -v c="$cores" -v j="$jobs" -v f="$fail" \
  'BEGIN{printf "PARALLEL wall %.2fs cores=%s jobs=%s fail=%s\n", e-s, c, j, f}'
if [[ $fail -eq 1 ]]; then
  exit "$fail_status"
fi
exit 0
