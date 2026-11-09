#!/usr/bin/env bash
# Root-only GPU lease for MAG Idol. NEVER accept arbitrary unit names.
set -euo pipefail
UNIT=mag-llm-coding@0.service
DIR=/run/mag-idol
MARKER="$DIR/lena-paused"
LOCK="$DIR/lease.lock"
[[ "$EUID" -eq 0 ]] || { echo 'root required' >&2; exit 1; }
install -d -m 0700 -o root -g root "$DIR"
exec 9>"$LOCK"
flock -x 9
case "$1" in
 acquire)
  if [[ -e "$MARKER" ]]; then
    echo 'exclusive MI50 lease already held' >&2
    exit 1
  fi
  if ! systemctl is-active --quiet "$UNIT"; then
    echo 'Lena already inactive: refusing exclusive GPU lease' >&2
    exit 1
  fi
  printf '%s\n' 'restore' > "$MARKER.tmp"
  chmod 0600 "$MARKER.tmp"
  mv -f "$MARKER.tmp" "$MARKER"
  # Queue stop first to suppress Restart=on-failure; no model drain.
  systemctl stop --no-block "$UNIT"
  systemctl kill --signal=SIGKILL --kill-who=all "$UNIT" 2>/dev/null || :
  for ((i=0;i<100;i++)); do
    PID="$(systemctl show --property=MainPID --value "$UNIT")"
    if [[ "$PID" == 0 ]] && ! systemctl is-active --quiet "$UNIT"; then
      echo 'Lena stopped; MI50 lease acquired'
      exit 0
    fi
    sleep 0.1
  done
  echo 'Lena did not stop; refusing GPU job' >&2
  exit 1
  ;;
 restore)
  [[ -e "$MARKER" ]] || exit 0
  # Preserve marker until systemd accepts restart request.
  systemctl start --no-block "$UNIT"
  rm -f "$MARKER"
  echo 'Lena restart scheduled'
  ;;
 status)
  [[ -e "$MARKER" ]]
  ;;
 *)
  echo 'usage: gpu-lease.sh {acquire|restore|status}' >&2
  exit 2
  ;;
esac
