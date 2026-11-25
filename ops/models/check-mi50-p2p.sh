#!/usr/bin/env bash
# Read-only PCI inspection; optional bounded HIP test under the existing GPU lock.
set -euo pipefail
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
mode=${1:-}
[[ $# == 3 && ( $mode == inspect || $mode == test ) ]] || { echo "Usage: bash $0 inspect|test PCI_BDF_A PCI_BDF_B" >&2; exit 2; }
shift
for id in "$@"; do
  [[ $id =~ ^[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-7]$ ]] || exit 2
done
[[ $1 != "$2" ]] || exit 2
python3 - "$@" <<'PY'
import pathlib, sys
GiB=1<<30
TiB=1<<40
nodes=[]
ready=True
for bdf in sys.argv[1:]:
    p=pathlib.Path('/sys/bus/pci/devices')/bdf
    def read(name): return (p/name).read_text().strip()
    if read('vendor')!='0x1002' or (p/'driver').resolve().name!='amdgpu':
        raise SystemExit('Not an amdgpu device: '+bdf)
    node=int(read('numa_node')); nodes.append(node)
    total=int(read('mem_info_vram_total'))
    visible=int(read('mem_info_vis_vram_total'))
    resources=[tuple(int(x,16) for x in l.split()) for l in read('resource').splitlines()]
    print(f'{bdf}: NUMA {node}, total {total/GiB:.3f} GiB, visible {visible/GiB:.3f} GiB')
    if not (31*GiB<=total<=32*GiB and visible>=total): ready=False
    for idx in (0,2):
        start,end,flags=resources[idx]
        size=end-start+1 if start else 0
        print(f'  BAR{idx}: {start:#x}..{end:#x} ({size/GiB:.6f} GiB), base {start/TiB:.6f} TiB')
        if not (start and end<16*TiB and size>0 and flags&0x200): ready=False
        if idx==0 and size<32*GiB: ready=False
    for q in [p.resolve(),*p.resolve().parents]:
        if (q/'current_link_width').exists():
            print(' ',q.name,(q/'current_link_speed').read_text().strip(),'x'+(q/'current_link_width').read_text().strip())
    print('  IOMMU group:',(p/'iommu_group').resolve().name if (p/'iommu_group').exists() else 'none')
print('Kernel:', pathlib.Path('/proc/sys/kernel/osrelease').read_text().strip())
print('Kernel command line:',pathlib.Path('/proc/cmdline').read_text().strip())
print('NUMA0 / full BAR / 44-bit address preconditions:', 'PASS' if ready and nodes==[0,0] else 'NOT MET')
# Diagnostic only: the subsequent peer test is decisive; no firmware writes.
PY
[[ $mode == test ]] || exit 0
source "$ROOT/ops/host.sh"
ROCM=$(readlink -f "$MAG_IDOL_BASE/rocm-6.3.4/current")
[[ -x $ROCM/lib/llvm/bin/clang++ ]] || { echo "Existing MAG ROCm missing" >&2; exit 2; }
tmp=$(mktemp -d)
trap 'rm -rf -- "$tmp"' EXIT
"$ROCM/lib/llvm/bin/clang++" -x hip --offload-arch=gfx906 --rocm-path="$ROCM" -O2 -std=c++17 \
  "$ROOT/ops/models/mi50-peer-test.hip.cpp" -L"$ROCM/lib" -lamdhip64 -Wl,-rpath,"$ROCM/lib" -o "$tmp/peer-test"
lock=${MAG_GPU_LOCK:-/var/lib/mag-idol/gpu.lock}
# Do not create locks, stop services or evict a model. Operator must free GPU first.
exec 9<"$lock"
flock -n 9 || { echo "GPU busy; stop Qwen via normal supervisor before testing" >&2; exit 2; }
exec 8<"$lock.idol"
flock -n 8 || { echo "Idol demand pending" >&2; exit 2; }
flock -u 8
unset HIP_VISIBLE_DEVICES ROCR_VISIBLE_DEVICES CUDA_VISIBLE_DEVICES
timeout --kill-after=5s 30s "$tmp/peer-test" "$@"
