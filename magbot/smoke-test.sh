#!/usr/bin/env bash
# Real on-host E2E. Unique test bot; collect diagnostics before safe teardown.
set -Eeuo pipefail
export LC_ALL=C
if [[ "${EUID}" -ne 0 ]]; then echo 'Run: sudo bash smoke-test.sh' >&2; exit 1; fi
command -v flock >/dev/null || { echo 'Missing flock (util-linux)' >&2; exit 1; }
exec 9>/run/lock/magbot-smoke-test.lock
flock -n 9 || { echo 'Another MAGBOT smoke test is running' >&2; exit 1; }
NAME="smoke-$(date +%s)-$$"
DOMAIN="magbot-${NAME}"
LOGDIR=/var/log/magbot
install -d -m 0700 "$LOGDIR"
LOGFILE="$LOGDIR/smoke-${NAME}.log"
umask 077
exec > >(tee -a "$LOGFILE") 2>&1
STARTED=0
passed=0
print_diagnostics() {
  echo "=== Diagnostic snapshot for ${NAME} ==="
  magbot inspect "$NAME" || true
  virsh -c qemu:///system dominfo "$DOMAIN" || true
  echo '=== Registry directory ==='
  ls -la "/var/lib/libvirt/images/magbot/bots/$NAME" || true
  echo '=== Recent libvirt host log ==='
  journalctl --no-pager -n 35 -u libvirtd -u virtqemud 2>/dev/null || true
  echo '=== Guest bootstrap log, if guest agent is responsive ==='
  magbot logs "$NAME" || true
}
cleanup() {
  local original_rc=$?
  trap - EXIT
  if (( STARTED )); then
    if (( original_rc != 0 )); then print_diagnostics; fi
    echo "=== Despawn ${NAME} ==="
    if ! magbot despawn "$NAME"; then
      echo 'CRITICAL: despawn failed, inspect the registry/VM manually; no destructive fallback' >&2
      original_rc=1
    fi
    if virsh -c qemu:///system dominfo "$DOMAIN" >/dev/null 2>&1 || [[ -e "/var/lib/libvirt/images/magbot/bots/$NAME" ]]; then
      echo 'CRITICAL: orphan remains after despawn; manual inspection required' >&2
      original_rc=1
    else
      echo 'CLEANUP VERIFIED: no libvirt domain or guest directory remains'
    fi
  fi
  echo "Diagnostics: $LOGFILE"
  exit "$original_rc"
}
trap cleanup EXIT

echo "Test ID: $NAME"
magbot doctor
# Mark cleanup intention before spawn: if it fails during provisioning, inspect first.
STARTED=1
magbot spawn "$NAME"
for ((i=1; i<=90; i++)); do
  if ! state="$(magbot status "$NAME" 2>&1)"; then
    echo "E2E FAILED: status command exited nonzero at poll $i: $state" >&2
    exit 1
  fi
  echo "[$i/90] $state"
  if [[ "$state" =~ [[:space:]]ready[[:space:]] ]]; then
    magbot exec "$NAME" -- /opt/magbot/venv/bin/python /opt/magbot/health.py
    passed=1
    echo 'E2E PASSED: VM, QEMU guest agent, Chromium, Playwright'
    exit 0
  fi
  if [[ "$state" =~ [[:space:]]failed[[:space:]] || "$state" =~ [[:space:]]stopped[[:space:]] ]]; then
    echo "E2E FAILED: guest state: $state" >&2
    exit 1
  fi
  sleep 10
done
echo 'E2E FAILED: VM did not become ready within allotted time' >&2
exit 1
