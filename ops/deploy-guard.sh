#!/usr/bin/env bash
# Shared deploy lock and deadline. Source this; do not execute it.
# The lock is held by a supervisor process, not by this shell. This shell's
# children therefore cannot inherit it. An orphaned curl used to keep
# /run/lock/mag-private-deploy.lock after a deploy was killed during HTTP 429,
# so the next deploy reported that another install was running.
# flock's FD_CLOEXEC flag is per process. A child Python cannot set it on
# the shell's fd, so the supervisor opens the lock itself and the deploy
# process never has that fd.
MAG_LOCK_PY='import fcntl, os, sys, signal
path = sys.argv[1]
cmd = sys.argv[2:]
if not cmd:
    sys.stderr.write("deploy lock: missing command\n")
    raise SystemExit(2)
fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
message = os.environ.get("MAG_DEPLOY_LOCK_MESSAGE", "Another MAG deploy/ingress install is running")
try:
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
except OSError:
    sys.stderr.write(message + "\n")
    raise SystemExit(int(os.environ.get("MAG_DEPLOY_LOCK_EXIT", "1")))
pid = os.fork()
if pid == 0:
    os.close(fd)
    os.execvp(cmd[0], cmd)
    os._exit(127)
_, status = os.waitpid(pid, 0)
code = os.waitstatus_to_exitcode(status)
if code < 0:
    signal.signal(-code, signal.SIG_DFL)
    os.kill(os.getpid(), -code)
raise SystemExit(code)
'

# Re-exec under the lock supervisor. Returns only in the restarted script,
# which does not hold the lock fd. A busy lock exits MAG_DEPLOY_LOCK_EXIT.
mag_deploy_lock() {
  if [[ -n ${MAG_DEPLOY_LOCK_HELD:-} ]]; then
    return 0
  fi
  local path=${MAG_DEPLOY_LOCK:-/run/lock/mag-private-deploy.lock}
  mkdir -p "$(dirname "$path")"
  command -v python3 >/dev/null 2>&1 || { echo 'python3 is required to hold the deploy lock' >&2; return 1; }
  export MAG_DEPLOY_LOCK_HELD=1
  exec python3 -c "$MAG_LOCK_PY" "$path" bash "$0" "$@"
}

# Re-exec once under timeout(1). timeout kills the process group, so a hung
# child cannot keep the deploy alive. This does not take the lock: deploy.sh
# holds /run/lock/mag-private-deploy.lock on fd 8 via the watchdog, and other
# callers take it with mag_deploy_lock. Taking it here as well would make
# that flock fail closed.
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

# shellcheck source=mag-units.sh
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/mag-units.sh"

# Every systemctl wait in a deploy has a deadline. A backup snapshot is
# allowed a longer budget than a status query; neither waits forever.
mag_systemctl() {
  if [[ ${1:-} == daemon-reload && ${MAG_UNITS_USE_SYSTEMCTL_WRAPPER:-1} == 1 ]]; then
    mag_daemon_reload
    return
  fi
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
