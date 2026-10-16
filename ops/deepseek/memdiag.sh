#!/usr/bin/env bash
# Wo liegt der Speicher des laufenden llama? Klärt die "Doppelkopie" (AnonPages ~138 GiB neben 142 GiB
# gesperrtem Page-Cache) anhand von /proc/<pid>/smaps – nur lesend.
#   - Datei-Mappings der GGUF-Teile: Rss, gesperrt, Anonymous (= Copy-on-Write, falls llama hineinschreibt)
#   - große anonyme Bereiche (>= 1 GiB): Repack-Puffer (CPU_REPACK) bzw. eigene Gewichtskopie
#   sudo bash ops/deepseek/memdiag.sh [Ausgabedatei]
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "bitte mit sudo"; exit 1; }
pid=$(systemctl show -p MainPID --value mag-llm.service)
[[ -n "$pid" && "$pid" != 0 ]] || { echo "mag-llm läuft nicht"; exit 1; }
OUT=${1:-/dev/stdout}
python3 -I - "$pid" > "$OUT" <<'PY'
import re, sys
pid = sys.argv[1]
cmd = open(f"/proc/{pid}/cmdline", "rb").read().replace(b"\0", b" ").decode(errors="replace")
maps, cur = [], None
for line in open(f"/proc/{pid}/smaps"):
    m = re.match(r"^([0-9a-f]+)-([0-9a-f]+) (\S+) \S+ \S+ \S+\s*(.*)$", line)
    if m:
        cur = {"size": int(m.group(2), 16) - int(m.group(1), 16), "perm": m.group(3), "name": m.group(4).strip()}
        maps.append(cur); continue
    k = line.split(":", 1)
    if cur is not None and len(k) == 2 and k[1].strip().endswith("kB"):
        cur[k[0]] = int(k[1].split()[0]) * 1024
G = 2 ** 30
gguf = [m for m in maps if m["name"].endswith(".gguf")]
anon_big = [m for m in maps if not m["name"] or m["name"].startswith("[")]
anon_big = [m for m in anon_big if m.get("Rss", 0) >= G]
s = lambda L, k: sum(m.get(k, 0) for m in L)
print(f"PID {pid}: {cmd[:200]}")
print(f"Prozess gesamt: Rss {s(maps,'Rss')/G:.1f} GiB, gesperrt {s(maps,'Locked')/G:.1f} GiB, anonym {s(maps,'Anonymous')/G:.1f} GiB")
print(f"GGUF-Datei-Mappings: {len(gguf)}  Rss {s(gguf,'Rss')/G:.1f} GiB  gesperrt {s(gguf,'Locked')/G:.1f} GiB  "
      f"Copy-on-Write (Anonymous) {s(gguf,'Anonymous')/G:.1f} GiB  Rechte {sorted(set(m['perm'] for m in gguf))}")
print(f"Große anonyme Bereiche (>= 1 GiB): {len(anon_big)}  Rss {s(anon_big,'Rss')/G:.1f} GiB  gesperrt {s(anon_big,'Locked')/G:.1f} GiB  "
      f"THP {s(anon_big,'AnonHugePages')/G:.1f} GiB")
for m in sorted(anon_big, key=lambda m: -m.get("Rss", 0))[:12]:
    print(f"   {m['size']/G:8.1f} GiB  Rss {m.get('Rss',0)/G:7.1f}  gesperrt {m.get('Locked',0)/G:7.1f}  {m['perm']} {m['name'] or '[anon]'}")
cow, big = s(gguf, "Anonymous"), s(anon_big, "Rss")
if big >= 50 * G:
    v = (f"Doppelkopie = {big/G:.0f} GiB anonyme Puffer neben den Datei-Seiten. Bei mmap ist das der Repack-"
         "Puffer (CPU_REPACK: llama sortiert die Gewichte für AVX2 um und hält sie zusätzlich). "
         "Abhilfe: --no-repack (Gewichte direkt aus den Datei-Seiten) – nur sinnvoll, wenn nicht langsamer.")
elif cow >= 50 * G:
    v = f"Doppelkopie = {cow/G:.0f} GiB Copy-on-Write in den Datei-Mappings (llama schreibt in gemappte Gewichte)."
else:
    v = "keine nennenswerte Doppelkopie"
print("Befund: " + v)
PY
[[ "$OUT" == /dev/stdout ]] || cat "$OUT"
