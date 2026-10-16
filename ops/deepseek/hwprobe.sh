#!/usr/bin/env bash
# Bestandsaufnahme von Hardware/Kernel/llama.cpp für das Performance-Tuning von mag-llm.
# Nur lesend – ändert nichts. Als root ausführen (dmidecode, /proc/<pid>).
#
#   sudo bash ops/deepseek/hwprobe.sh > /tmp/mag-hwprobe.txt
set -uo pipefail
sec() { printf '\n===== %s =====\n' "$1"; }
have() { command -v "$1" >/dev/null 2>&1; }

sec "System"; uname -a; cat /etc/os-release | head -3; uptime
sec "CPU (lscpu)"; lscpu
sec "CPU-Flags (Auszug)"; grep -m1 -o -wE 'avx|avx2|fma|f16c|avx512[a-z_]*|amx[a-z_]*|avx_vnni|bmi2' /proc/cpuinfo | sort -u | tr '\n' ' '; echo
sec "NUMA"; if have numactl; then numactl --hardware; else echo "numactl fehlt (apt install numactl)"; fi
cat /proc/sys/kernel/numa_balancing 2>/dev/null | sed 's/^/numa_balancing=/'
cat /proc/sys/vm/zone_reclaim_mode 2>/dev/null | sed 's/^/zone_reclaim_mode=/'
sec "Speicher"; free -g; grep -E 'MemTotal|MemAvailable|Mlocked|HugePages_|Hugepagesize|AnonHugePages|FileHugePages|ShmemHugePages' /proc/meminfo
sec "Transparent Huge Pages"; for f in enabled defrag shmem_enabled khugepaged/defrag; do printf '%s: ' "$f"; cat /sys/kernel/mm/transparent_hugepage/$f 2>/dev/null; done
sec "RAM-Module (Kanäle, Takt)"
if have dmidecode; then
  dmidecode -t memory | awk -F: '/^\tSize:|^\tLocator:|^\tBank Locator:|^\tSpeed:|^\tConfigured Memory Speed:|^\tType:|^\tRank:/ {gsub(/^[ \t]+/,"",$2); printf "%s=%s  ", $1, $2} /^$/ {print ""}' | grep -v 'Size=No Module' | grep 'Size=' | sed 's/\t//g'
else echo "dmidecode fehlt"; fi
sec "CPU-Takt/Governor"
cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor 2>/dev/null | sed 's/^/governor=/'
cat /sys/devices/system/cpu/intel_pstate/no_turbo 2>/dev/null | sed 's/^/intel_pstate.no_turbo=/'
cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_driver 2>/dev/null | sed 's/^/driver=/'
grep -m4 'MHz' /proc/cpuinfo
have cpupower && cpupower idle-info 2>/dev/null | head -20
sec "Kernel-Cmdline"; cat /proc/cmdline
sec "Speicherbandbreite grob (falls mbw/stream vorhanden)"; have mbw && mbw -q -n 3 1024 2>/dev/null | tail -2 || echo "(mbw nicht installiert – optional: apt install mbw)"
sec "llama.cpp"
runuser -u chris -- /home/chris/.local/bin/llama --version 2>&1 | head -3
runuser -u chris -- /home/chris/.local/bin/llama --help 2>&1 | head -40
sec "mag-llm Unit (aktiv)"; systemctl cat mag-llm.service 2>/dev/null | sed -n '/ExecStart=/,/^$/p'
systemctl show mag-llm.service -p ActiveState -p ExecMainStartTimestamp -p MemoryCurrent
pid=$(systemctl show -p MainPID --value mag-llm.service 2>/dev/null)
if [[ -n "$pid" && "$pid" != 0 ]]; then
  sec "mag-llm Prozess $pid"
  grep -E 'VmLck|VmRSS|VmHWM|Threads' /proc/$pid/status
  have numastat && numastat -p "$pid" 2>/dev/null | tail -8
  grep -E 'AnonHugePages|FilePmdMapped' /proc/$pid/smaps_rollup 2>/dev/null
fi
sec "llama system_info / Ladeprotokoll (letzter Start)"
journalctl -u mag-llm --no-pager -o cat -b 2>/dev/null | grep -iE 'system_info|AVX|n_threads|numa|mlock|mmap|load time|model size|n_ctx|CPU_REPACK|repack|KV|warn' | tail -40
sec "Gemessene Geschwindigkeit (Audit-Log, letzte 30 Antworten)"
python3 -I - <<'PY'
import glob, json, os
files = sorted(glob.glob("/var/log/mag-audit/*/*/*/events.jsonl"))[-3:]
rows = []
for f in files:
    for line in open(f, encoding="utf-8"):
        try: r = json.loads(line)
        except Exception: continue
        if r.get("type") != "llm_response": continue
        t = (r.get("body") or {}).get("timings") or {}
        if t: rows.append((r.get("ts","")[:19], t.get("prompt_n"), t.get("cache_n"), t.get("prompt_per_second"), t.get("predicted_n"), t.get("predicted_per_second"), r.get("duration_s")))
print(f"{'Zeit':19} {'prompt_n':>8} {'cache_n':>8} {'pp t/s':>8} {'gen_n':>6} {'tg t/s':>7} {'Dauer s':>8}")
for z,pn,cn,pp,gn,tg,d in rows[-30:]:
    f=lambda v,fmt: (fmt % v) if isinstance(v,(int,float)) else "-"
    print(f"{z:19} {f(pn,'%8d'):>8} {f(cn,'%8d'):>8} {f(pp,'%8.1f'):>8} {f(gn,'%6d'):>6} {f(tg,'%7.2f'):>7} {f(d,'%8.1f'):>8}")
if not rows: print("(keine Timing-Daten im Audit-Log)")
PY
sec "Last/Prozesse"; top -b -n1 | head -15
sec "Agent-VMs"; /usr/local/bin/magbot status 2>/dev/null; virsh -c qemu:///system list --all 2>/dev/null
echo; echo "Fertig."
