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
if ! "$PY" -c 'import idol' >/dev/null 2>&1; then
  [[ -f $BASE/idol-sdk/pyproject.toml ]] || { echo 'Local idol-sdk missing' >&2; exit 1; }
  "$PY" -m pip install --no-input --disable-pip-version-check --no-deps -e "$BASE/idol-sdk"
fi
# Install missing mesh-only wheels without dependency resolution, preserving
# the known-good ROCm torch, numpy, patched Idol and encoder versions.
for spec in 'xatlas:xatlas' 'PIL:Pillow' 'fast_simplification:fast-simplification'; do
  module=${spec%%:*}
  package=${spec#*:}
  if ! "$PY" -c "import ${module}" >/dev/null 2>&1; then
    "$PY" -m pip install --no-input --disable-pip-version-check --no-deps "$package"
  fi
done
"$PY" - <<'PY'
import torch, tensordict, idol, scipy, skimage, trimesh, plyfile, xatlas, PIL, fast_simplification
assert torch.__version__.startswith("2.7.1+") and torch.version.hip
assert tensordict.__version__.split("+")[0] == "0.9.1"
PY
install -d -o chris -g chris -m 0700 /var/lib/mag-idol/jobs /var/lib/mag-idol/tmp
install -m 0644 /opt/mag-stack/current/ops/idol/mag-idol.tmpfiles.conf /etc/tmpfiles.d/mag-idol.conf
systemd-tmpfiles --create /etc/tmpfiles.d/mag-idol.conf
install -d -o root -g root -m 0755 /usr/local/lib/mag-idol
install -o root -g root -m 0755 /opt/mag-stack/current/ops/idol/power-cap.sh /usr/local/lib/mag-idol/power-cap.sh
# The GPU lease that paused Lena is gone (both bots are CPU-only), and the
# MIOpen cache paths of the first hotfix drop-in are part of the unit file.
rm -f /usr/local/lib/mag-idol/gpu-lease.sh /etc/sudoers.d/mag-idol
if grep -q '^Environment=MIOPEN_USER_DB_PATH=' /opt/mag-stack/current/ops/idol/mag-idol.service; then
  rm -f /etc/systemd/system/mag-idol.service.d/miopen.conf
  rmdir /etc/systemd/system/mag-idol.service.d 2>/dev/null || :
fi
install -m 0644 /opt/mag-stack/current/ops/idol/mag-idol.service /etc/systemd/system/mag-idol.service
systemctl daemon-reload
systemctl enable mag-idol.service
# Restart the worker only when its code or unit changed. Its jobs survive
# the restart: a stage runs on its own (KillMode=process) and the new worker
# follows it again, so a deploy never costs a photo-to-3D job. The one
# exception is a job that a worker from before this design started: it dies
# with that worker, so then the restart waits until the job is done.
rev=$(cat /opt/mag-stack/current/ops/idol/{worker,runner,mesh}.py /etc/systemd/system/mag-idol.service | sha256sum | cut -d' ' -f1)
if [[ $(cat /run/mag-idol.rev 2>/dev/null) != "$rev" ]] || ! systemctl is-active --quiet mag-idol.service; then
  active() { grep -lE '"state": *"(running|queued)"' /var/lib/mag-idol/jobs/*/status.json >/dev/null 2>&1; }
  health=$(curl --noproxy '*' -fsS --max-time 3 http://127.0.0.1:8793/api/idol/health 2>/dev/null || :)
  systemctl stop mag-idol-swap.service >/dev/null 2>&1 || :
  if [[ -n $health ]] && ! jq -e '.resumable == true' <<<"$health" >/dev/null 2>&1 && active; then
    echo 'mag-idol: a 3D job runs under the previous worker; the new worker takes over when it is done'
    systemd-run --quiet --collect --unit=mag-idol-swap --property=CPUAffinity=28-55 /bin/bash -c '
      for _ in $(seq 1 1080); do
        grep -lE "\"state\": *\"(running|queued)\"" /var/lib/mag-idol/jobs/*/status.json >/dev/null 2>&1 || break
        sleep 10
      done
      systemctl restart mag-idol.service'
  else
    systemctl restart mag-idol.service
  fi
  printf '%s\n' "$rev" > /run/mag-idol.rev
fi
for _ in {1..15}; do
  curl --noproxy '*' -fsS --max-time 3 http://127.0.0.1:8793/api/idol/health && break
  sleep 1
done
printf '\n'
