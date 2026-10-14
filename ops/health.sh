#!/usr/bin/env bash
set -euo pipefail
systemctl is-active --quiet mag-llm.service mag-120.service gitea.service tailscaled.service
curl --fail --silent --show-error --max-time 5 http://127.0.0.1:8766/readyz
printf '\n'
/usr/local/bin/magbot status
