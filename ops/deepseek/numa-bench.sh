#!/usr/bin/env bash
# Non-destructive before/after HTTP + NUMA capture against the same live Q4.
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
ACTION=before
[[ $# -eq 0 ]] || ACTION=$1
if [[ $ACTION == compare ]]; then
  [[ $# == 3 ]] || { echo "Usage: $0 compare BEFORE.md AFTER.md" >&2; exit 2; }
  python3 -I - "$2" "$3" <<'PY'
from pathlib import Path
import re,sys
def scan(path):
    data=Path(path).read_text()
    result={}
    for line in data.splitlines():
        cells=[x.strip() for x in line.strip('|').split('|')]
        if len(cells)==7 and cells[0] in ('tg128','pp512','pp2048','pp4096'):
            result[cells[0]]={'pp':float(cells[2]),'tg':float(cells[4]),'ttft':float(cells[5])}
    if 'tg128' not in result or 'pp2048' not in result: raise SystemExit('missing benchmark rows in '+path)
    return result
a,b=scan(sys.argv[1]),scan(sys.argv[2])
print('Metric                      before       after      change')
for task,key in (('tg128','tg'),('pp512','pp'),('pp2048','pp'),('pp4096','pp')):
    if task not in a or task not in b: continue
    x,y=a[task][key],b[task][key]
    print(f'{task:8} {key:12} {x:10.2f} {y:10.2f} {(y/x-1)*100:+9.1f}%' if x else f'{task}: zero baseline')
if b['tg128']['tg']<a['tg128']['tg']:
    raise SystemExit('NO-GO: slower generation; Ali should rollback')
print('Generation speed gate PASS; still inspect locality/cache/tools manually.')
PY
  exit
fi
[[ $ACTION =~ ^(before|after|rollback)$ ]] || { echo "Usage: $0 before|after|rollback|compare BEFORE.md AFTER.md" >&2; exit 2; }
P=$(systemctl show -p MainPID --value mag-llm.service)
[[ $P =~ ^[1-9][0-9]*$ ]] || { echo "No model PID" >&2; exit 3; }
stamp=$(date -u +%Y%m%dT%H%M%SZ)
OUT=/var/tmp/mag-numa-$ACTION-$stamp.txt
{
  date -u -Is
  echo "MODEL_PID=$P LABEL=$ACTION"
  systemctl show mag-llm.service -p ExecStart -p DropInPaths -p CPUAffinity -p NUMAPolicy -p NUMAMask
  grep -E '^(Cpus_allowed_list|Mems_allowed_list|VmRSS|VmLck):' /proc/"$P"/status
  numactl -H
  numastat -p "$P"
  cat /sys/devices/system/node/node0/meminfo | grep -E 'Mem(Free|Total)'
  cat /sys/devices/system/node/node1/meminfo | grep -E 'Mem(Free|Total)'
  systemctl is-active mag-model-pin.service || true
} > "$OUT"
echo "Hardware snapshot: $OUT"
# Same existing benchmark for both runs; never starts any model.
LABEL="numa-$ACTION-$stamp" bash "$ROOT/ops/deepseek/http-bench.sh"
