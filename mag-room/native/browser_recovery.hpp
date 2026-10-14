// Bounded guest-local restart of mag-browser only. Never touches Chromium,
// its CDP process, browser profile or other bots' processes.
#pragma once
#include <string>

// Called through the *selected guest's* magbot exec wrapper, running as the
// unprivileged bot user. Use the current effective UID so this can be tested
// safely on an isolated CI runner as well as in the guest.
inline std::string browser_controller_stop_script() {
  return R"MAGSTOP(
set -u
user_id="$(id -u)" || exit 90
# Ignore defunct/zombie processes. Double-forked daemons can briefly leave
# zombies until PID 1 reaps them; zombies do not hold the TCP/UDP ports.
active_controller() {
  ps -u "$user_id" -o stat=,comm= |
    awk '$2 == "mag-browser" && $1 !~ /^[ZzXx]/ {found=1} END {exit !found}'
}
if active_controller; then
  pkill -TERM -U "$user_id" -x mag-browser >/dev/null 2>&1 || true
  n=0
  while active_controller && [ "$n" -lt 25 ]; do
    sleep 0.2
    n=$((n + 1))
  done
  if active_controller; then
    # The hung controller may block CDP; force-stop only mag-browser and
    # only for this guest user. Chromium's process name is different.
    pkill -KILL -U "$user_id" -x mag-browser >/dev/null 2>&1 || true
    n=0
    while active_controller && [ "$n" -lt 15 ]; do
      sleep 0.2
      n=$((n + 1))
    done
  fi
fi
if active_controller; then
  echo "mag-browser still active after bounded TERM/KILL; guest diagnosis:"
  ps -u "$user_id" -o pid=,stat=,comm= | grep -E 'mag-browser$' || true
  exit 1
fi
exit 0
)MAGSTOP";
}
