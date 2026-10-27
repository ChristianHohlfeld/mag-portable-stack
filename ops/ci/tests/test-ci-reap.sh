#!/usr/bin/env bash
# mag-test-timeout: 60
if [ -z "${MAG_TEST_DEADLINE_ARMED:-}" ]; then
  export MAG_TEST_DEADLINE_ARMED=1
  exec timeout --kill-after=5s 60s bash "$0" "$@"
fi
# After a local CI run, no magroom it started is still listening. The sleeper
# calls setsid (the same escape as a test magroom) and ignores SIGTERM, so
# only an explicit kill of that session reaps it. A decoy in this test's own
# process group must still be alive: the runner only kills its descendants.
set -euo pipefail
root=$(cd -- "$(dirname "$0")/../../.." && pwd)
tmp=$(mktemp -d)
decoy=
cleanup() {
  if [[ -n ${decoy:-} ]]; then
    kill -KILL "$decoy" 2>/dev/null || true
    wait "$decoy" 2>/dev/null || true
  fi
  rm -rf "$tmp"
}
trap cleanup EXIT

cat > "$tmp/sleeper.py" << 'PY'
import ctypes, os, signal, socket, sys, time
# Show up as magroom in ps without execv: a fake argv0 is ENOENT on this interpreter.
libc = ctypes.CDLL(None, use_errno=True)
libc.prctl(15, b"magroom", 0, 0, 0)
signal.signal(signal.SIGTERM, signal.SIG_IGN)
signal.signal(signal.SIGINT, signal.SIG_IGN)
signal.signal(signal.SIGHUP, signal.SIG_IGN)
token, portfile = sys.argv[1], sys.argv[2]
sock = socket.socket()
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
sock.bind(("127.0.0.1", 0))
sock.listen(1)
with open(portfile, "w", encoding="ascii") as handle:
    handle.write("%s %s %s\n" % (os.getpid(), sock.getsockname()[1], token))
while True:
    time.sleep(3600)
PY
cat > "$tmp/leak.py" << 'PY'
import os, subprocess, sys, time
mode, token, portfile, sleeper = sys.argv[1:5]
subprocess.Popen([sys.executable, sleeper, token, portfile], start_new_session=True)
for _ in range(100):
    if os.path.isfile(portfile) and os.path.getsize(portfile) > 0:
        break
    time.sleep(0.05)
else:
    sys.exit("sleeper never bound a port")
if mode == "sleep":
    time.sleep(120)
elif mode != "exit":
    sys.exit("bad mode")
PY

leftover() { # leftover TOKEN — true when a process argv still contains it
  local token=$1 pid args
  for pid in /proc/[0-9]*; do
    [[ -r $pid/cmdline ]] || continue
    args=$(tr '\0' ' ' < "$pid/cmdline" 2>/dev/null || true)
    [[ $args == *"$token"* ]] || continue
    echo "leftover pid ${pid#/proc/}: $args" >&2
    return 0
  done
  return 1
}
port_open() {
  python3 -c 'import socket,sys
s=socket.socket()
s.settimeout(0.2)
try:
    s.connect(("127.0.0.1", int(sys.argv[1])))
except OSError:
    sys.exit(1)
sys.exit(0)' "$1"
}
read_port() { awk '{print $2; exit}' "$1"; }

assert_gone() { # assert_gone TOKEN PORTFILE
  local token=$1 file=$2 port
  [[ -s $file ]] || { echo "leak never started ($token)" >&2; exit 1; }
  port=$(read_port "$file")
  if leftover "$token"; then
    echo "magroom from this CI run still running ($token)" >&2
    exit 1
  fi
  if port_open "$port"; then
    echo "127.0.0.1:$port still accepting after CI ($token)" >&2
    exit 1
  fi
  kill -0 "$decoy"
}

# Decoy: same argv shape as a leaked magroom, but not a child of the CI run.
decoy_file=$tmp/decoy.port
decoy_token=magroom-decoy-$$
python3 "$tmp/sleeper.py" "$decoy_token" "$decoy_file" &
decoy=$!
for _ in $(seq 1 50); do
  [[ -s $decoy_file ]] && break
  sleep 0.05
done
[[ -s $decoy_file ]]
kill -0 "$decoy"

run_parallel() { # run_parallel TIMEOUT MODE TOKEN
  local limit=$1 mode=$2 token=$3 file
  file=$tmp/$token.port
  printf '%s\n' "leak :: python3 $tmp/leak.py $mode $token $file $tmp/sleeper.py" \
    | bash "$root/ops/ci/run-parallel.sh" --jobs 1 --timeout "$limit"
}

# 1) The command exits 0 and leaves a setsid magroom behind.
token=magroom-ci-exit-$$
run_parallel 15 exit "$token"
assert_gone "$token" "$tmp/$token.port"

# 2) The per-command timeout fires while the leaker is still asleep.
token=magroom-ci-timeout-$$
set +e
run_parallel 2 sleep "$token"
rc=$?
set -e
[[ $rc -ne 0 ]]
assert_gone "$token" "$tmp/$token.port"

# 3) Abort the runner itself (SIGTERM). The timeout has not fired yet.
token=magroom-ci-abort-$$
file=$tmp/$token.port
printf '%s\n' "leak :: python3 $tmp/leak.py sleep $token $file $tmp/sleeper.py" \
  | bash "$root/ops/ci/run-parallel.sh" --jobs 1 --timeout 30 &
runner=$!
for _ in $(seq 1 80); do
  [[ -s $file ]] && break
  sleep 0.05
done
[[ -s $file ]]
kill -TERM "$runner"
set +e
wait "$runner"
set -e
assert_gone "$token" "$file"

# 4) local-ci's own exit trap, not only run-parallel.
token=magroom-ci-local-$$
file=$tmp/$token.port
MAG_CI_REAP_PROBE="python3 $tmp/leak.py exit $token $file $tmp/sleeper.py" \
  bash "$root/ops/ci/local-ci.sh"
assert_gone "$token" "$file"

echo "ci reap: no magroom from the CI run is still listening"
