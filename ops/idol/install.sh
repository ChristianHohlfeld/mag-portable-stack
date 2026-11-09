#!/usr/bin/env bash
# Optional MAG Idol service only. Preserve already-working ROCm torch and patch.
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "root required" >&2; exit 1; }
BASE=/home/chris/idol-mi50
PY="$BASE/venv/bin/python"
[[ -x $PY && -d $BASE/weights/idol && -d $BASE/weights/dinov3-timm && -d $BASE/weights/flux2vae ]] || {
  echo 'Idol MI50 venv/local weights missing; optional worker skipped' >&2
  exit 0
}
# Never allow idol-sdk dependency resolution to replace ROCm torch with CUDA.
"$PY" - <<'PY'
import torch
assert torch.__version__.startswith("2.7.1+") and torch.version.hip, \
    "Expected torch 2.7.1+rocm6.3 in the pre-existing Idol venv"
PY
# Exact pin without transitive dependencies; no torch/ROCm changes.
if ! "$PY" -c 'import tensordict; assert tensordict.__version__.split("+")[0]=="0.9.1"' 2>/dev/null; then
  "$PY" -m pip install --no-input --disable-pip-version-check --no-deps 'tensordict==0.9.1'
fi
# Use the existing editable idol-sdk, including ~/idol-mi50/mi50.patch.
# Reinstall editable metadata only if missing; never fetch dependency upgrades.
if ! "$PY" -m pip show idol-sdk >/dev/null 2>&1; then
  [[ -f $BASE/idol-sdk/pyproject.toml ]] || { echo 'Local idol-sdk missing' >&2; exit 1; }
  "$PY" -m pip install --no-input --disable-pip-version-check --no-deps -e "$BASE/idol-sdk"
fi
# Mesh requirements must already exist on the verified host. Missing dependencies
# fail with a clear message instead of automatic package graph upgrades.
"$PY" - <<'PY'
import torch, tensordict, idol, scipy, skimage, trimesh, plyfile
assert torch.__version__.startswith("2.7.1+") and torch.version.hip
assert tensordict.__version__.split("+")[0] == "0.9.1"
PY
install -d -o chris -g chris -m 0700 /var/lib/mag-idol/jobs
# Root-only lease helper; sudoers permits only these fixed actions.
install -d -o root -g root -m 0755 /usr/local/lib/mag-idol
install -o root -g root -m 0755 /opt/mag-stack/current/ops/idol/gpu-lease.sh /usr/local/lib/mag-idol/gpu-lease.sh
install -o root -g root -m 0440 /opt/mag-stack/current/ops/idol/mag-idol.sudoers /etc/sudoers.d/mag-idol
visudo -cf /etc/sudoers.d/mag-idol
install -m 0644 /opt/mag-stack/current/ops/idol/mag-idol.service /etc/systemd/system/mag-idol.service
systemctl daemon-reload
systemctl enable mag-idol.service
systemctl restart mag-idol.service
curl --fail --silent --show-error --retry 3 --retry-delay 1 --max-time 5 \
  http://127.0.0.1:8793/api/idol/health
printf '\n'
