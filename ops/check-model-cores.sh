#!/usr/bin/env bash
# Warn when a non-llama thread uses more than ${MAG_CORE_WARN_PCT:-5}% of a
# model core 0-27 (NUMA0 0-13, NUMA1 14-27). The 14 lockstep llama threads per
# node stall on any foreign thread (live: VM vCPUs and cc1plus, decode 6-8
# instead of 17-18 tok/s). Warning only; exit 0.
set -uo pipefail
max_core="${MAG_MODEL_CORE_MAX:-27}" warn="${MAG_CORE_WARN_PCT:-5}" secs="${MAG_CORE_SAMPLE_S:-3}"
snap() {
  for st in /proc/[0-9]*/task/[0-9]*/stat; do
    read -r line < "$st" 2>/dev/null || continue
    comm="${line#*(}"; comm="${comm%)*}"; rest="${line##*) }"
    read -ra f <<<"$rest"
    # rest fields: f[11]=utime f[12]=stime f[36]=processor
    id="${st#/proc/}"; id="${id%/stat}"; id="${id/\/task\//\/}"
    printf '%s %s %s %s\n' "$id" "$comm" "$(( f[11] + f[12] ))" "${f[36]}"
  done
}
a=$(snap); sleep "$secs"; b=$(snap)
hz=$(getconf CLK_TCK)
out=$(awk -v hz="$hz" -v s="$secs" -v mc="$max_core" -v w="$warn" '
  NR==FNR {t[$1]=$3; next}
  ($1 in t) && $2 !~ /^llama-server$/ && $4 <= mc {
    pct = ($3 - t[$1]) * 100 / hz / s
    if (pct > w) printf "model-cores: WARN %s (%s) %.0f%% on cpu %d\n", $2, $1, pct, $4
  }' <(printf '%s\n' "$a") <(printf '%s\n' "$b"))
if [[ -n $out ]]; then printf '%s\n' "$out" >&2; else echo "model-cores: OK (no foreign thread >${warn}% on cpus 0-${max_core})"; fi
exit 0
