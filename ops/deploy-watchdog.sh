#!/usr/bin/env bash
# Sourced by ops/deploy.sh. Arms one deadline for the whole deploy and runs
# each probe/curl/wait in its own process group. On the deadline those groups
# are killed, then the deploy shell is signalled SIGTERM so its EXIT trap
# (deploy_exit) still runs and rolls back once the release switch has started.
# The lock fd is close-on-exec: a child that outlives the shell cannot keep
# /run/lock/mag-private-deploy.lock.
[[ "${BASH_SOURCE[0]}" != "$0" ]] || { echo 'source ops/deploy-watchdog.sh' >&2; exit 1; }

# fcntl in a child cannot set FD_CLOEXEC on this shell. Bash children source
# BASH_ENV and close the lock fd; deploy_run closes it before exec. Either
# way a probe cannot keep the flock after this shell is gone.
deploy_lock_cloexec() {
  local fd=${1:-8} drop prev
  drop=$(mktemp)
  prev=${BASH_ENV:-}
  {
    printf 'exec %s>&- 2>/dev/null || true\n' "$fd"
    if [[ -n $prev && -f $prev ]]; then printf '. %q\n' "$prev"; fi
  } >"$drop"
  export BASH_ENV=$drop
  DEPLOY_LOCK_DROP=$drop
}

_deploy_same_proc() {
  local pid=$1 stamp=$2 now
  [[ -r /proc/$pid/stat ]] || return 1
  now=$(awk '{print $22}' "/proc/$pid/stat" 2>/dev/null || true)
  [[ -n $now && $now == "$stamp" ]]
}

_deploy_kill_pid() {
  local sig=$1 pid=$2 deploy_pgid=$3 pgid
  [[ -n $pid ]] || return 0
  pgid=$(ps -o pgid= -p "$pid" 2>/dev/null | tr -d ' ' || true)
  if [[ -n $pgid && $pgid != "$deploy_pgid" ]]; then
    kill -"$sig" -"$pgid" 2>/dev/null || true
  else
    kill -"$sig" "$pid" 2>/dev/null || true
  fi
}

_deploy_descendants() {
  local parent=$1 d pid ppid
  for d in /proc/[0-9]*; do
    [[ -r $d/status ]] || continue
    pid=${d#/proc/}
    ppid=$(awk '/^PPid:/ {print $2; exit}' "$d/status" 2>/dev/null || true)
    [[ $ppid == "$parent" ]] || continue
    printf '%s\n' "$pid"
    _deploy_descendants "$pid"
  done
}

# Recorded deploy_run leaders, plus any other children of the deploy shell.
# Never signals the deploy shell itself; the caller does that after, so the
# EXIT trap can run.
_deploy_kill_children() {
  local sig=$1 deploy=$2 list=$3 stamp=$4 deploy_pgid pid
  _deploy_same_proc "$deploy" "$stamp" || return 0
  deploy_pgid=$(ps -o pgid= -p "$deploy" 2>/dev/null | tr -d ' ' || true)
  if [[ -f $list ]]; then
    while read -r pid; do
      [[ $pid == "$deploy" ]] && continue
      _deploy_kill_pid "$sig" "$pid" "$deploy_pgid"
    done <"$list"
  fi
  while read -r pid; do
    [[ $pid == "$deploy" ]] && continue
    _deploy_kill_pid "$sig" "$pid" "$deploy_pgid"
  done < <(_deploy_descendants "$deploy")
}

deploy_watchdog_fire() {
  local deadline=$1 deploy=$2 list=$3 stamp=$4 grace=${5:-120}
  sleep "$deadline"
  _deploy_same_proc "$deploy" "$stamp" || exit 0
  echo "deploy watchdog: deadline ${deadline}s exceeded; killing child process groups" >&2
  _deploy_kill_children TERM "$deploy" "$list" "$stamp"
  _deploy_same_proc "$deploy" "$stamp" || exit 0
  kill -TERM "$deploy" 2>/dev/null || true
  sleep "$grace"
  _deploy_same_proc "$deploy" "$stamp" || exit 0
  echo "deploy watchdog: shell still alive ${grace}s after the deadline; killing it" >&2
  _deploy_kill_children KILL "$deploy" "$list" "$stamp"
  kill -KILL "$deploy" 2>/dev/null || true
}

deploy_watchdog_arm() {
  local seconds=$1 stamp
  [[ $seconds =~ ^[0-9]+$ ]] || { echo "bad deploy deadline: $seconds" >&2; return 1; }
  (( seconds > 0 )) || { echo 'deploy deadline must be > 0' >&2; return 1; }
  DEPLOY_CHILD_LIST=$(mktemp)
  stamp=$(awk '{print $22}' /proc/$$/stat)
  # Own session, so the sleeper is not killed by the signal it sends.
  # shellcheck disable=SC2016 # inner shell expands the positional parameters
  setsid bash -c 'exec 8>&- 2>/dev/null || true; source "$1"; deploy_watchdog_fire "$2" "$3" "$4" "$5" "$6"' _ \
    "${BASH_SOURCE[0]}" "$seconds" "$$" "$DEPLOY_CHILD_LIST" "$stamp" \
    "${MAG_DEPLOY_ROLLBACK_GRACE_S:-120}" &
  DEPLOY_WATCHDOG_PID=$!
}

deploy_watchdog_stop() {
  local pid=${DEPLOY_WATCHDOG_PID:-}
  DEPLOY_WATCHDOG_PID=
  if [[ -n $pid ]]; then
    kill -TERM -"$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null || true
    wait "$pid" 2>/dev/null || true
  fi
  if [[ -n ${DEPLOY_CHILD_LIST:-} && -f ${DEPLOY_CHILD_LIST} ]]; then
    rm -f "$DEPLOY_CHILD_LIST"
  fi
  DEPLOY_CHILD_LIST=
  if [[ -n ${DEPLOY_LOCK_DROP:-} && -f ${DEPLOY_LOCK_DROP} ]]; then
    rm -f "$DEPLOY_LOCK_DROP"
  fi
  DEPLOY_LOCK_DROP=
}

# Run a probe, curl, or wait in its own process group. The budget kills that
# group; the overall deadline kills it too, even when curl is asleep in a
# Retry-After that --max-time does not cover.
deploy_run() {
  local budget=$1 pid start rc=0
  shift
  [[ ${1:-} == -- ]] && shift
  [[ -n ${DEPLOY_CHILD_LIST:-} && -f ${DEPLOY_CHILD_LIST} ]] || {
    echo 'deploy_run: watchdog is not armed' >&2
    return 1
  }
  (( budget > 0 )) || budget=1
  # Close the lock fd in this process, then replace it with the command in
  # its own session. $! stays the command: setsid execs without forking.
  bash -c 'exec 8>&- 2>/dev/null || true; exec setsid "$@"' _ "$@" &
  pid=$!
  printf '%s\n' "$pid" >>"$DEPLOY_CHILD_LIST"
  start=$SECONDS
  while kill -0 "$pid" 2>/dev/null; do
    if (( SECONDS - start >= budget )); then
      echo "deploy_run: ${budget}s timeout, killing process group $pid" >&2
      kill -TERM -"$pid" 2>/dev/null || true
      sleep 1
      kill -KILL -"$pid" 2>/dev/null || true
      wait "$pid" 2>/dev/null || true
      return 124
    fi
    sleep 0.2
  done
  wait "$pid" || rc=$?
  return "$rc"
}

deploy_exit() {
  local rc=$?
  trap - EXIT ERR INT TERM
  deploy_watchdog_stop || true
  if (( rc != 0 && ${deployment_started:-0} )); then
    rollback || echo 'Rollback incomplete: retain SSH rescue channel and inspect /var/backups/mag-stack' >&2
  fi
  exit "$rc"
}
