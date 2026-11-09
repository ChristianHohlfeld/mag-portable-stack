#!/usr/bin/env bash
# Optional installer, called after release activation. No LLM restarts.
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "root required" >&2; exit 1; }
[[ -x /home/chris/idol-mi50/venv/bin/python ]] || { echo "Idol venv absent: skipping" >&2; exit 0; }
[[ -d /home/chris/idol-mi50/weights/idol ]] || { echo "Idol weights absent: skipping" >&2; exit 0; }
# Only install missing mesh dependencies into the existing Idol venv.
# Does not replace torch/ROCm or touch any LLM environment.
if ! /home/chris/idol-mi50/venv/bin/python -c 'import scipy, skimage, trimesh, plyfile' >/dev/null 2>&1; then
  /home/chris/idol-mi50/venv/bin/python -m pip install --disable-pip-version-check --no-input \
    'scipy>=1.14,<1.16' 'scikit-image>=0.24,<0.26' 'trimesh>=4.4,<4.8' || {
      echo 'Idol mesh packages unavailable; optional service not started' >&2
      exit 1
    }
fi
# Optional face decimation; an unavailable wheel must never block GLB/STL.
/home/chris/idol-mi50/venv/bin/python -m pip show fast-simplification >/dev/null 2>&1 || \
  /home/chris/idol-mi50/venv/bin/python -m pip install --disable-pip-version-check --no-input \
    'fast-simplification>=0.1,<0.2' || true
install -d -o chris -g chris -m 0700 /var/lib/mag-idol/jobs
install -m 0644 /opt/mag-stack/current/ops/idol/mag-idol.service /etc/systemd/system/mag-idol.service
systemctl daemon-reload
systemctl enable mag-idol.service
systemctl restart mag-idol.service
# Check only that the worker's queue HTTP endpoint is ready, no model load.
curl --fail --silent --show-error --max-time 5 http://127.0.0.1:8793/api/idol/health
echo
