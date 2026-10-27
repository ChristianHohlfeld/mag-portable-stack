#!/usr/bin/env bash
# Sourced by local-ci.sh and run-parallel.sh. Not a test.
[[ ${BASH_SOURCE[0]} != "$0" ]] || { echo 'source ops/ci/reap.sh' >&2; exit 1; }
# The runner stays in its caller's process group (an outer timeout and Ctrl-C
# must still reach it). Descendant commands run in their own session; this
# trap kills those sessions on exit, timeout, and abort.

# Only this worktree's magroom. Production /opt/mag-stack/.../magroom is left alone.
mag_ci_worktree_magroom() {
  readlink -f "${MAG_CI_MAGROOM:-${ROOT:-.}/build/magroom}"
}

mag_ci_worktree_magroom_pids() {
  local magroom pid exe
  magroom=$(mag_ci_worktree_magroom)
  [[ -n $magroom && -e $magroom ]] || return 0
  for pid in /proc/[0-9]*; do
    pid=${pid#/proc/}
    exe=$(readlink -f "/proc/$pid/exe" 2>/dev/null || true)
    if [[ "$exe" == "$magroom" ]]; then
      printf '%s\n' "$pid"
    fi
  done
}

# Prints killed pids to stderr. Returns 1 when any leftover existed.
mag_ci_kill_worktree_magroom() {
  local leftover
  mapfile -t leftover < <(mag_ci_worktree_magroom_pids)
  if ((${#leftover[@]})); then
    echo "killing leftover magroom from this worktree: ${leftover[*]}" >&2
    kill -KILL "${leftover[@]}" 2>/dev/null || true
    return 1
  fi
  return 0
}

mag_ci_reap_exit() {
  [[ -n ${MAG_CI_REAP_BIN:-} ]] || return 0
  python3 "$MAG_CI_REAP_BIN" --kill "$$" || true
}

mag_ci_arm() {
  local reap=$1
  shift
  if [[ -z ${MAG_CI_REAPER:-} ]]; then
    export MAG_CI_REAPER=1
    exec python3 "$reap" --self bash "$0" "$@"
  fi
  unset MAG_CI_REAPER
  MAG_CI_REAP_BIN=$reap
  trap mag_ci_reap_exit EXIT
  trap 'mag_ci_reap_exit; exit 130' INT
  trap 'mag_ci_reap_exit; exit 143' TERM
  trap 'mag_ci_reap_exit; exit 129' HUP
}
