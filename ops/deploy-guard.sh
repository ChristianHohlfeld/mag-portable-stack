#!/usr/bin/env bash
# Shared deploy lock and deadline. Source this; do not execute it.
# Children must not inherit the lock fd. An orphaned curl kept
# /run/lock/mag-private-deploy.lock (fd 8) after a deploy was killed during
# an HTTP 429, so the next deploy reported that another install was running.
mag_cloexec() {
  local fd=$1
  python3 -c 'import fcntl,sys; fcntl.fcntl(int(sys.argv[1]), fcntl.F_SETFD, fcntl.FD_CLOEXEC)' "$fd"
}

# Acquire MAG_DEPLOY_LOCK (default /run/lock/mag-private-deploy.lock) on fd.
# flock is non-blocking. The fd is close-on-exec before this function returns.
mag_deploy_lock() {
  local fd=${1:-8}
  local path=${MAG_DEPLOY_LOCK:-/run/lock/mag-private-deploy.lock}
  mkdir -p "$(dirname "$path")"
  eval "exec ${fd}>\"\$path\""
  flock -n "$fd" || { echo 'Another MAG deploy/ingress install is running' >&2; return 1; }
  mag_cloexec "$fd"
}

# Re-exec this process under timeout(1) once. timeout kills the process
# group, so a hung child cannot keep the deploy alive. The lock is taken
# after the re-exec and is not inherited (mag_cloexec).
mag_deploy_arm_deadline() {
  if [[ -n ${MAG_DEPLOY_DEADLINE_ARMED:-} ]]; then
    return 0
  fi
  local deadline=${MAG_DEPLOY_DEADLINE_S:-3600}
  [[ "$deadline" =~ ^[0-9]+$ && "$deadline" -ge 1 ]] || {
    echo 'MAG_DEPLOY_DEADLINE_S must be a positive number of seconds' >&2
    return 1
  }
  command -v timeout >/dev/null 2>&1 || { echo 'timeout(1) is required so a deploy cannot wait forever' >&2; return 1; }
  export MAG_DEPLOY_DEADLINE_ARMED=1
  exec timeout --kill-after=5s "${deadline}s" bash "$0" "$@"
}

# Every systemctl wait in a deploy has a deadline. A backup snapshot is
# allowed a longer budget than a status query; neither waits forever.
mag_systemctl() {
  local budget=${MAG_SYSTEMD_BUDGET_S:-180}
  case "${1:-}" in
    is-active|is-enabled|is-failed|cat|show|status|daemon-reload)
      budget=${MAG_SYSTEMD_QUERY_S:-20} ;;
  esac
  local arg
  for arg in "$@"; do
    if [[ "$arg" == mag-backup-deploy.service ]]; then
      budget=${MAG_BACKUP_BUDGET_S:-1800}
    fi
  done
  timeout --kill-after=10s "${budget}s" systemctl "$@"
}
