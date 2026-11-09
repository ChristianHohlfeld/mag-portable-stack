#!/usr/bin/env bash
# Optional installer, called after release activation. No LLM restarts.
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "root required" >&2; exit 1; }
[[ -x /home/chris/idol-mi50/venv/bin/python ]] || { echo "Idol venv absent: skipping" >&2; exit 0; }
[[ -d /home/chris/idol-mi50/weights/idol ]] || { echo "Idol weights absent: skipping" >&2; exit 0; }
install -d -o chris -g chris -m 0700 /var/lib/mag-idol/jobs
install -m 0644 /opt/mag-stack/current/ops/idol/mag-idol.service /etc/systemd/system/mag-idol.service
systemctl daemon-reload
systemctl enable mag-idol.service
systemctl restart mag-idol.service
# Check only that the worker's queue HTTP endpoint is ready, no model load.
curl --fail --silent --show-error --max-time 5 http://127.0.0.1:8793/api/idol/health
echo
