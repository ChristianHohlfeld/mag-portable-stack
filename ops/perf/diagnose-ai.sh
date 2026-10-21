#!/usr/bin/env bash
# Read-only MAG AI performance diagnostic. No model requests, restarts or secrets.
set -euo pipefail
PATH=/usr/sbin:/usr/bin:/sbin:/bin
echo "MAG AI hardware / cache audit (read-only)"
date -Is
echo "== CPU, physical sockets, NUMA"
lscpu | grep -E '^(Architecture|Model name|CPU\(s\)|Thread\(s\) per core|Core\(s\) per socket|Socket\(s\)|NUMA node[0-9]+ CPU\(s\)):' || true
if command -v numactl >/dev/null; then numactl -H | head -n 18; fi
echo "== Memory pressure"
grep -E '^(MemTotal|MemAvailable|Cached|SwapTotal|SwapFree|Mlocked|AnonPages):' /proc/meminfo || true
for f in /sys/devices/system/node/node*/meminfo; do
  echo "$f"
  grep -E 'MemTotal|MemFree|FilePages|Shmem|MemUsed' "$f" | head -n 5 || true
done
for f in /proc/pressure/{cpu,memory,io}; do
  echo "$f"; [[ -f "$f" ]] && head -n 2 "$f"
done
echo "== Model services (no PID equals not resident)"
for svc in mag-llm mag-llm-qwen mag-llm-iq3 mag-120 mag-audit-gateway; do
  printf '%s active=%s enabled=%s ' "$svc" \
    "$(systemctl is-active "$svc" 2>/dev/null || true)" \
    "$(systemctl is-enabled "$svc" 2>/dev/null || true)"
  pid=$(systemctl show -p MainPID --value "$svc" 2>/dev/null || true)
  printf 'pid=%s\n' "$pid"
  if [[ "$pid" =~ ^[1-9][0-9]*$ && -r /proc/$pid/status ]]; then
    grep -E '^(VmRSS|VmLck|RssAnon|RssFile|Threads|Cpus_allowed_list|Mems_allowed_list):' "/proc/$pid/status" || true
    command -v numastat >/dev/null && numastat -p "$pid" 2>/dev/null | tail -n 6 || true
    # Never echo full command line (can contain credentials).
    if [[ $svc == mag-llm || $svc == mag-llm-qwen || $svc == mag-llm-iq3 ]]; then
      python3 -I - "$pid" <<'PY'
import pathlib,sys
p=pathlib.Path('/proc',sys.argv[1],'cmdline')
a=p.read_bytes().split(b'\0')
args=[x.decode('utf-8','replace') for x in a if x]
keep={'--ctx-size','--parallel','--threads','--threads-batch','--batch-size',
      '--ubatch-size','--numa','--cache-ram','-crs','--cache-ram-similarity'}
shown={}
for i,s in enumerate(args[:-1]):
    if s in keep: shown[s]=args[i+1]
print('safe_flags='+repr(shown))
PY
    fi
  fi
done
echo "== localhost health (never issue a completion or evict cache)"
for port in 8090 8091; do
  printf 'model:%s status=' "$port"
  curl --noproxy '*' --max-time 3 -s -o /dev/null -w '%{http_code}\n' "http://127.0.0.1:$port/health" || true
done
echo "== Last prompt cache measurements (no prompt or tool contents)"
journalctl -u mag-120.service -n 200 --no-pager -o cat 2>/dev/null | \
  grep -E '^prompt-cache slot=' | tail -n 8 || true
echo "== Kernel page faults / swap activity"
grep -E '^(pgmajfault|pswpin|pswpout) ' /proc/vmstat || true
echo "== Interpretation"
echo 'Cache misses after restart: expected once. Repeated cached=0 for identical bot prefix: real bug.'
echo 'Q4 (NUMA0) and Qwen (NUMA1) should each show local memory and 14 physical worker threads.'
echo 'Cold CPU prefill is proportional to uncached tokens; check model=... prompt_tps=... and tokens_cached.'
echo 'Inspect MAG_ROOM_CONTEXT_BYTES/TRANSCRIPT_BYTES and active model environment with systemctl show mag-120 -p Environment.'
echo 'Pinned model pages (VmLck) are NOT identical to cached chat tokens (tokens_cached).'
