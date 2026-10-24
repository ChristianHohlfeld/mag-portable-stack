#!/usr/bin/env bash
# Run as root via systemd ExecStartPre=+ (MAG audit firewall denies chris
# direct access to :8090). Q4 may need minutes to load after cold boot.
set -euo pipefail
# DeepSeek is retired: without an enabled/active mag-llm there is no Q4 to wait for,
# so Qwen (the Ornith rollback target) starts on its own.
if ! systemctl is-active --quiet mag-llm.service && ! systemctl is-enabled --quiet mag-llm.service; then
  echo 'Qwen boot gate: DeepSeek Q4 retired (mag-llm disabled); no gate'
  exit 0
fi
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
