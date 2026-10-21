#!/usr/bin/env bash
# Run as root via systemd ExecStartPre=+ (MAG audit firewall denies chris
# direct access to :8090). Q4 may need minutes to load after cold boot.
set -euo pipefail
for ((i=0; i<1080; i++)); do
  if /usr/bin/curl --noproxy '*' -fsS -o /dev/null --max-time 5 http://127.0.0.1:8090/health; then
    echo 'Qwen boot gate: Q4 is ready on NUMA0'
    exit 0
  fi
  if (( i % 6 == 0 )); then echo 'Qwen boot gate: waiting for Q4 readiness ...'; fi
  sleep 10
done
echo 'Qwen boot gate: Q4 still unavailable; refusing unguarded NUMA1 start' >&2
exit 4
