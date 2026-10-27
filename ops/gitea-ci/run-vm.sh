#!/usr/bin/env bash
set -euo pipefail
cd /var/lib/mag-ci/vm
[[ -s job.qcow2 && -s seed.iso ]] || exit 1
# No 9p, virtiofs, host block devices, SSH forward or host paths in the guest.
# The only writable disk is a disposable overlay over a root-owned base image.
exec /usr/bin/qemu-system-x86_64 -enable-kvm -cpu host -smp 2 -m 12288 \
  -name mag-ci-ephemeral -nographic -nodefaults -no-reboot \
  -drive file=job.qcow2,if=virtio,format=qcow2,cache=none \
  -drive file=seed.iso,media=cdrom,format=raw,readonly=on \
  -netdev tap,id=ci,ifname=magci0,script=no,downscript=no \
  -device virtio-net-pci,netdev=ci,mac=52:54:00:fe:ff:02 \
  -serial null -monitor none -display none
