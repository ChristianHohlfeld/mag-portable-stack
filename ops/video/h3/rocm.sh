#!/bin/bash
# ROCm 6.3.4's HIP compiler and libraries for building stable-diffusion.cpp's
# HIP backend for the MI50 (gfx906): only unpacked into the owner's directory
# (~/idol-mi50/rocm-6.3.4), never installed into the system, so nothing else
# on the host sees or uses it. The kernel's amdgpu driver stays as it is.
# Every package is checked against the SHA256 of AMD's package index.
set -e
BASE=${MAG_IDOL_BASE:-$HOME/idol-mi50}/rocm-6.3.4
REPO=https://repo.radeon.com/rocm/apt/6.3.4
WANT="hipcc hip-dev hip-runtime-amd rocm-llvm rocm-device-libs comgr hsa-rocr hsa-rocr-dev rocm-core rocblas rocblas-dev
hipblas hipblas-dev hipblas-common-dev hipblaslt hipblaslt-dev rocsolver rocm-cmake rocprofiler-register rocminfo"
mkdir -p "$BASE/debs" "$BASE/root"; cd "$BASE"
exec 9>"$BASE/.lock"
flock -n 9 || { echo 'another rocm.sh is running' >&2; exit 1; }
curl -fsSL "$REPO/dists/noble/main/binary-amd64/Packages.gz" | gunzip > Packages
for name in $WANT; do
  entry=$(awk -v p="$name" 'BEGIN{RS=""} $0 ~ "(^|\n)Package: " p "\n" {print; exit}' Packages)
  file=$(sed -n 's/^Filename: //p' <<<"$entry"); sum=$(sed -n 's/^SHA256: //p' <<<"$entry")
  [ -n "$file" ] && [ -n "$sum" ] || { echo "not in the index: $name" >&2; exit 2; }
  deb="debs/$(basename "$file")"
  [ -s "$deb" ] || curl -fsSL --retry 10 -o "$deb.part" "$REPO/$file" && [ -s "$deb.part" ] && mv "$deb.part" "$deb" || :
  echo "$sum  $deb" | sha256sum --quiet -c - || { echo "WRONG $deb" >&2; exit 3; }
  dpkg-deb -x "$deb" root
done
ln -sfn "$(ls -d root/opt/rocm-6.3.4)" current
ls current/lib/rocblas/library | grep -q gfx906 || { echo 'rocBLAS has no gfx906 kernels' >&2; exit 4; }
echo done
