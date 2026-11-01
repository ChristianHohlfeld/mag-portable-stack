#!/usr/bin/env bash
# Warn (never fail/restart) if the loaded Mistral process has >2 GiB on NUMA1.
set -euo pipefail
TIMEOUT=${MISTRAL_NUMA_CHECK_TIMEOUT:-24000}
PORT=8191
warn(){ echo "Mistral NUMA placement WARN: $*" >&2; exit 0; }
[[ $TIMEOUT =~ ^[1-9][0-9]*$ ]] || warn "invalid check timeout: $TIMEOUT"
command -v numastat >/dev/null 2>&1 || warn 'numastat is unavailable'
command -v curl >/dev/null 2>&1 || warn 'curl is unavailable'
deadline=$((SECONDS + TIMEOUT))
until curl --noproxy '*' -fsS --max-time 5 "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; do
  (( SECONDS < deadline )) || warn "Mistral :$PORT did not become healthy within $TIMEOUT seconds"
  sleep 5
done
pid=$(systemctl show --property=MainPID --value mag-llm-mistral-room.service)
[[ $pid =~ ^[1-9][0-9]*$ ]] || warn "cannot determine Mistral MainPID: $pid"
stats=$(numastat -p "$pid" 2>/dev/null) || warn "numastat -p $pid failed"
node1=$(awk '$1 == "Total" { print $3; found=1 } END { if (!found) exit 1 }' <<<"$stats") || warn 'could not parse numastat total row'
if awk -v mib="$node1" 'BEGIN { exit !(mib > 2048) }'; then
  echo "Mistral NUMA placement WARN: NUMA1=$node1 MiB (>2048 MiB); inspect numastat -p $pid" >&2
else
  echo "Mistral NUMA placement PASS: NUMA1=$node1 MiB"
fi
exit 0
