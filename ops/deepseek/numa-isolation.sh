#!/usr/bin/env bash
# Stage strict NUMA-0 isolation. NO mag-llm restart in this script.
set -eo pipefail
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
ACTION=preflight
if [[ $# -gt 0 ]]; then ACTION=$1; fi
ENVFILE=/etc/mag-llm/llm.env
UNIT=/etc/systemd/system/mag-llm.service
OLD=/etc/systemd/system/mag-llm.service.d/50-ik.conf
GUARD=/etc/systemd/system/mag-llm.service.d/90-node0.conf
IQ_GUARD=/etc/systemd/system/mag-llm-iq3.service.d/90-sequential-hotswap-required.conf
PIN_GUARD=/etc/systemd/system/mag-model-pin.service.d/90-numa-retired.conf
BACK=/var/lib/mag-llm/numa-rollback
fail() { echo "NUMA: $*" >&2; exit 4; }
[[ $EUID == 0 ]] || fail "root required"
case "$ACTION" in
rollback)
  [[ -f "$BACK/READY" && -f "$BACK/llm.env" && -f "$BACK/unit" ]] || fail "rollback snapshot missing"
  install -m 0600 "$BACK/llm.env" "$ENVFILE"
  install -m 0644 "$BACK/unit" "$UNIT"
  if [[ -e "$BACK/old" ]]; then install -D -m 0644 "$BACK/old" "$OLD"; else rm -f "$OLD"; fi
  rm -f "$GUARD"
  systemctl daemon-reload
  echo "Original config restored. No model restart; vmtouch and IQ3 stay blocked until reviewed safe reconfiguration."
  exit 0;;
preflight|stage|verify) ;;
*) fail "usage: preflight|stage|verify|rollback";;
esac
P=$(systemctl show -p MainPID --value mag-llm.service)
[[ "$P" =~ ^[1-9][0-9]*$ && -r "/proc/$P/status" ]] || fail "mag-llm not running"
if [[ "$ACTION" == verify ]]; then
  curl --noproxy '*' -fsS --max-time 5 http://127.0.0.1:8090/health >/dev/null || fail "unhealthy"
  [[ $(awk '/^Cpus_allowed_list:/{print $2}' "/proc/$P/status") == "0-13,28-41" ]] || fail "CPUs not node0"
  grep -q 'bind:0' "/proc/$P/numa_maps" || fail "no bind memory policy"
  # Large anonymous allocations are model weights. No remote pages allowed there.
  python3 -I - "$P" <<'PY'
from pathlib import Path
import re,sys
total=remote=0
for line in Path('/proc',sys.argv[1],'numa_maps').read_text().splitlines():
    a=re.search(r'\banon=(\d+)',line)
    if not a or int(a[1])<131072: continue  # >=512 MiB anon VMA
    n0=re.search(r'\bN0=(\d+)',line)
    n1=re.search(r'\bN1=(\d+)',line)
    total+=int(n0[1]) if n0 else 0
    remote+=int(n1[1]) if n1 else 0
print(f'Large anonymous VMAs: N0={total} pages, N1={remote} pages')
if total < 8*1024*1024 or remote:
    raise SystemExit('NUMA FAIL: missing local model buffers or remote node1 pages')
PY
  systemctl is-active --quiet mag-model-pin && fail "double lock still active"
  numastat -p "$P"
  echo "Inspect model anonymous N1 counts in /proc/$P/numa_maps; no remote model pages permitted."
  exit 0
fi
[[ -r "$ENVFILE" && -f "$UNIT" ]] || fail "missing env/unit"
[[ $(cat /sys/devices/system/node/node0/cpulist) == "0-13,28-41" ]] || fail "node0 CPU topology mismatch"
[[ $(cat /sys/devices/system/node/node1/cpulist) == "14-27,42-55" ]] || fail "node1 CPU topology mismatch"
command -v numactl >/dev/null && command -v numastat >/dev/null || fail "numactl required"
curl --noproxy '*' -fsS --max-time 5 http://127.0.0.1:8090/health >/dev/null || fail "Q4 unhealthy"
[[ ! -e "$BACK/READY" && ! -e "$GUARD" && ! -e "$IQ_GUARD" ]] || fail "already staged"
[[ $(awk '/Node 0 MemTotal:/{print $4}' /sys/devices/system/node/node0/meminfo) -gt 230000000 ]] || fail "node0 capacity too small"
tr '\0' '\n' < "/proc/$P/cmdline" | grep -Fxq -- '-rtr' || fail "Q4 not -rtr"
tr '\0' '\n' < "/proc/$P/cmdline" | grep -Fxq -- '--mlock' || fail "Q4 not --mlock"
if grep -Eq '/home/chris/models/deepseek-v4/.*[.]gguf' "/proc/$P/maps"; then fail "Q4 uses mmap; cache pin required"; fi
systemctl is-active --quiet mag-llm-iq3.service && fail "IQ3 already resident; sequential swap required"
EXE=$(readlink -f "/proc/$P/exe")
[[ -x "$EXE" && "$EXE" == */llama-server ]] || fail "not ik_llama.cpp server"
for dir in /etc/systemd/system/mag-llm.service.d /run/systemd/system/mag-llm.service.d; do
  [[ -d "$dir" ]] || continue
  for drop in "$dir"/*.conf; do
    [[ -f "$drop" && "$drop" != "$OLD" ]] || continue
    ! grep -q '^ExecStart=' "$drop" || fail "unknown ExecStart override $drop"
  done
done
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
python3 -I - "$ENVFILE" "$TMP/env" "$EXE" <<'PY'
from pathlib import Path
import sys
lines=Path(sys.argv[1]).read_text().splitlines()
override={'ENGINE':'ik','IK_BIN':sys.argv[3],'NUMA_ARGS':'"--cpunodebind=0 --membind=0"',
          'LLAMA_NUMA':'numactl','THREADS':'16','THREADS_BATCH':'16','IK_RTR':'1'}
out=[]
for line in lines:
    key=line.split('=',1)[0]
    if key=='IK_ENV':
        v=' '.join(x for x in line.split('=',1)[1].strip('"').split() if not x.startswith('MAG_NUMA_LOCAL='))
        if v: out.append('IK_ENV="'+v+'"')
    elif key not in override: out.append(line)
out += [k+'='+v for k,v in override.items()]
Path(sys.argv[2]).write_text('\n'.join(out)+'\n')
PY
chmod 0600 "$TMP/env"
(
  export MAG_LLM_ENV="$TMP/env"
  unset ENGINE IK_BIN NUMA_ARGS LLAMA_NUMA THREADS THREADS_BATCH IK_RTR IK_ENV IK_ARGS IK_CRS IK_SLOT_SAVE IK_SLOT_SAVE_PATH
  source "$ROOT/ops/deepseek/llm-config.sh"
  render_unit "$TMP/unit"
)
for token in '--cpunodebind=0 --membind=0' '--numa numactl' '--threads 16' '--threads-batch 16' '--mlock' '-rtr' '--parallel 1' '--cache-type-k f16' '--cache-type-v f16'; do
  grep -Fq -- "$token" "$TMP/unit" || fail "render missing $token"
done
! grep -q 'Environment=MAG_NUMA_LOCAL=' "$TMP/unit" || fail "custom cross-node placement still enabled"
python3 -I - "$P" "$TMP/unit" <<'PY'
from pathlib import Path
import re,shlex,sys
live=Path('/proc',sys.argv[1],'cmdline').read_bytes().rstrip(b'\0').decode().split('\0')
txt=Path(sys.argv[2]).read_text()
m=re.search(r'(?ms)^ExecStart=(.*?)(?=^\S|\Z)',txt)
assert m, 'ExecStart absent'
new=shlex.split(m.group(1).replace('\\\n',' '))
names=('--cache-ram','-cram','--slot-save-path','-crs','--cache-ram-similarity')
def cache(xs): return {s:xs[i+1] for i,s in enumerate(xs[:-1]) if s in names}
assert cache(live)==cache(new), f'prompt cache drift: {cache(live)} -> {cache(new)}'
required=('--model','--alias','--ctx-size','--chat-template-file')
def get(xs,flag):
    if flag not in xs: raise AssertionError('live/staged missing '+flag)
    return xs[xs.index(flag)+1]
for flag in required:
    assert get(live,flag)==get(new,flag), f'{flag}: actual model/template contract changed'

PY
if [[ "$ACTION" == preflight ]]; then
  echo "Preflight PASS; NO host changes. Q4, cache, threads, topology checked."
  exit 0
fi
install -d -m 0700 "$BACK"
cp -p "$ENVFILE" "$BACK/llm.env"; cp -p "$UNIT" "$BACK/unit"
[[ ! -e "$OLD" ]] || cp -p "$OLD" "$BACK/old"
touch "$BACK/READY"  # complete rollback point BEFORE first host mutation
cat > "$TMP/pin-guard" <<'EOF'
[Unit]
RefuseManualStop=no
ConditionPathExists=/run/mag-model-pin-explicitly-enabled
EOF
cat > "$TMP/node-guard" <<'EOF'
[Service]
# Hard node0 placement. Conflicts= is unsafe: it may stop an active Q4.
CPUAffinity=0-13 28-41
NUMAPolicy=bind
NUMAMask=0
EOF
cat > "$TMP/iq3-guard" <<'EOF'
[Service]
# PR #31 concurrently starts Q4 and IQ3: cannot fit on node0.
# Explicit startup lock. Remove only in a reviewed sequential hot-swap PR.
# Unlike Conflicts=, this cannot implicitly stop a running Q4.
ExecStartPre=/usr/bin/false
EOF
install -D -m 0644 "$TMP/iq3-guard" "$IQ_GUARD"
install -D -m 0644 "$TMP/pin-guard" "$PIN_GUARD"
systemctl daemon-reload
systemctl disable --now mag-model-pin.service || fail "failed to stop double pin; DO NOT restart"
systemctl is-active --quiet mag-model-pin && fail "vmtouch still active"
bash "$ROOT/ops/deepseek/warm.sh" evict-cache || true
free0=$(awk '/Node 0 MemFree:/{print $4}' /sys/devices/system/node/node0/meminfo)
[[ "$free0" =~ ^[0-9]+$ ]] && ((free0 >= 32*1024*1024)) || fail "node0 low free RAM: DO NOT restart Q4"
# Strict restart-fit gate: free node0 pages plus Q4 anonymous pages
# releasable at Ali's manual restart must cover all current private weights
# and 12 GiB of runtime headroom. Missing NUMA accounting -> FAIL CLOSED.
rssanon=$(awk '/^RssAnon:/{print $2}' "/proc/$P/status")
local_anon=$(python3 -I - "$P" <<'PY'
from pathlib import Path
import os,re,sys
total=0
for line in Path('/proc',sys.argv[1],'numa_maps').read_text().splitlines():
    a=re.search(r'\banon=(\d+)',line)
    n=re.search(r'\bN0=(\d+)',line)
    if a and n: total+=min(int(a[1]),int(n[1]))
print(total*(os.sysconf('SC_PAGE_SIZE')//1024))
PY
)
[[ "$rssanon" =~ ^[0-9]+$ && "$local_anon" =~ ^[0-9]+$ ]] || fail "model NUMA accounting unavailable"
(( rssanon >= 80*1024*1024 && local_anon >= 32*1024*1024 )) || fail "unexpected weight memory"
(( free0 + local_anon >= rssanon + 12*1024*1024 )) || fail "node0 cannot fit private Q4 weights plus reserve: DO NOT RESTART"
install -m 0600 "$TMP/env" "$ENVFILE"
install -m 0644 "$TMP/unit" "$UNIT"
rm -f "$OLD"
install -D -m 0644 "$TMP/node-guard" "$GUARD"
systemctl daemon-reload
EFFECTIVE=$(systemctl show -p ExecStart --value mag-llm.service)
[[ "$EFFECTIVE" == *'--cpunodebind=0'* && "$EFFECTIVE" == *'--membind=0'* && "$EFFECTIVE" == *'--numa numactl'* ]] || fail "effective ExecStart wrong"
touch "$BACK/READY"
echo "STAGED, NO mag-llm restart. Ali performs manual restart after baseline and coordination."
