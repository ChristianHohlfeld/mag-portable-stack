#!/usr/bin/env bash
# Kernel reports global MemAvailable but no equivalent for each NUMA node.
# Conservatively estimate node0 reclaimable clean page cache + free pages,
# capping it at global MemAvailable. Never count shmem or dirty pages.
set -euo pipefail
node="${MAG_NUMA_NODE0_MEMINFO:-/sys/devices/system/node/node0/meminfo}"
global="${MAG_NUMA_GLOBAL_MEMINFO:-/proc/meminfo}"
[[ -r "$node" && -r "$global" ]] || exit 4
awk '
FNR==NR { if ($1=="Node" && $2=="0") m[$3]=$4+0;next }
$1=="MemAvailable:" { g=$2+0 }
END {
  total=m["MemTotal:"];free=m["MemFree:"];
  file=m["FilePages:"];shmem=m["Shmem:"];
  dirty=m["Dirty:"];wb=m["Writeback:"];
  slab=m["SReclaimable:"];
  if(total<230000000 || free<=0 || g<=0)exit 4;
  clean=file-shmem-dirty-wb-file/20;
  if(clean<0)clean=0;
  avail=free+clean+slab/2;
  if(avail>g)avail=g;
  if(avail>total)avail=total;
  printf "%.0f\n",avail;
}' "$node" "$global"
