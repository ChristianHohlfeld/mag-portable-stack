#!/usr/bin/env bash
# Pin every managed bot VM (vCPUs + qemu emulator thread) to the HT siblings
# 28-55, live and in the persistent libvirt config. Model cores 0-27 belong to
# the lockstep llama.cpp threads (NUMA0 0-13, NUMA1 14-27).
# Run on the MAG host: sudo bash ops/vm/pin-vcpus.sh
set -euo pipefail
cpus="${MAG_VM_CPUSET:-28-55}"
vir() { virsh -c qemu:///system "$@"; }
for id in $(vir list --all --name | grep '^magbot-' || true); do
  n=$(vir vcpucount "$id" --maximum --config)
  live=0; [[ "$(vir domstate "$id")" == running ]] && live=1
  for (( v=0; v<n; v++ )); do
    vir vcpupin "$id" "$v" "$cpus" --config >/dev/null
    (( live )) && vir vcpupin "$id" "$v" "$cpus" --live >/dev/null
  done
  vir emulatorpin "$id" "$cpus" --config >/dev/null
  (( live )) && vir emulatorpin "$id" "$cpus" --live >/dev/null
  echo "$id: $n vCPU + emulator pinned to $cpus"
done
