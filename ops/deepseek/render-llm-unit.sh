#!/usr/bin/env bash
# Erzeugt die mag-llm-Unit aus ops/mag-llm.service für den installierten llama-Build.
#  - setzt @LOAD_ARGS@: Laden OHNE mmap (Gewichte im eigenen Prozessspeicher, damit llama sie für
#    AVX2 umsortieren kann), mit mlock, sofern der Build das anbietet – ermittelt aus `llama serve --help`
#  - prüft jedes übrige Flag gegen dieselbe Hilfe; bei Unbekanntem Abbruch, nichts wird geschrieben
#
#   render-llm-unit.sh TEMPLATE ZIEL           schreibt ZIEL (atomar)
#   render-llm-unit.sh TEMPLATE --check        nur prüfen, Ergebnis anzeigen
# Umgebung: HELP_FILE=<datei>  Hilfetext statt `llama serve --help` (Tests)
#           LOAD_ARGS="..."    Lademodus explizit vorgeben (überspringt die Erkennung)
set -euo pipefail
TPL=${1:?Vorlage fehlt}; DST=${2:?Ziel oder --check fehlt}
BIN=/home/chris/.local/bin/llama
# ab 3.6: gespeicherte Startwerte aus /etc/mag-llm/llm.env (tune.sh / upgrade-3.6.sh); die Umgebung hat
# Vorrang. Mit HELP_FILE (Tests) nur, wenn MAG_LLM_ENV ausdrücklich gesetzt ist.
if [[ -n "${MAG_LLM_ENV:-}" || -z "${HELP_FILE:-}" ]]; then
  _envf=${MAG_LLM_ENV:-/etc/mag-llm/llm.env}
  if [[ -r "$_envf" ]]; then
    while IFS='=' read -r k v; do
      v=${v%\"}; v=${v#\"}
      case "$k" in LOAD_ARGS|THREADS|THREADS_BATCH|BATCH|UBATCH|LLAMA_BIN|EXTRA_ARGS|NUMA_ARGS|LLAMA_NUMA|ENGINE|IK_BIN|IK_RTR|IK_ARGS|IK_ENV) [[ -n "${!k:-}" ]] || printf -v "$k" '%s' "$v" ;; esac
    done < <(grep -E '^(LOAD_ARGS|THREADS|THREADS_BATCH|BATCH|UBATCH|LLAMA_BIN|EXTRA_ARGS|NUMA_ARGS|LLAMA_NUMA|ENGINE|IK_BIN|IK_RTR|IK_ARGS|IK_ENV)=' "$_envf" || true)
  fi
fi
# ab 3.8: ik_llama.cpp -> eigener Renderer (gleiche Vorlage, anderer ExecStart)
if [[ "${ENGINE:-llama}" == ik ]]; then
  export IK_BIN="${IK_BIN:-}" THREADS="${THREADS:-28}" THREADS_BATCH="${THREADS_BATCH:-28}" UBATCH="${UBATCH:-1024}"
  export BATCH="${BATCH:-$UBATCH}" NUMA_ARGS="${NUMA_ARGS:-}" LLAMA_NUMA="${LLAMA_NUMA:-}" IK_RTR="${IK_RTR:-1}" IK_ARGS="${IK_ARGS:-}" IK_ENV="${IK_ENV:-}"
  [[ "$NUMA_ARGS" == - ]] && NUMA_ARGS=""; [[ "$LLAMA_NUMA" == - ]] && LLAMA_NUMA=""
  exec bash "$(dirname "$(readlink -f "$0")")/render-ik-unit.sh" "$TPL" "$DST"
fi
# ab 3.7: anderes llama-Binary (selbst gebaut, ops/deepseek/build-llama.sh); Hilfe und Flags kommen dann von dort
if [[ -n "${LLAMA_BIN:-}" ]]; then
  [[ "$LLAMA_BIN" =~ ^/[A-Za-z0-9._/+-]+$ ]] || { echo "render: LLAMA_BIN '$LLAMA_BIN' ist kein gültiger absoluter Pfad" >&2; exit 4; }
  [[ -x "$LLAMA_BIN" || -n "${HELP_FILE:-}" ]] || { echo "render: LLAMA_BIN '$LLAMA_BIN' ist nicht ausführbar" >&2; exit 4; }
  BIN=$LLAMA_BIN
fi
if [[ -n "${HELP_FILE:-}" ]]; then help=$(cat "$HELP_FILE")
elif [[ $EUID -eq 0 ]]; then help=$(runuser -u chris -- "$BIN" serve --help 2>&1 || true)
else help=$("$BIN" serve --help 2>&1 || true); fi
[[ -n "$help" ]] || { echo "render: keine Ausgabe von 'llama serve --help'" >&2; exit 2; }

if [[ -z "${LOAD_ARGS:-}" ]]; then
  LOAD_ARGS=$(python3 -I - "$help" <<'PY'
import re, sys
h = sys.argv[1]
def has_flag(f): return re.search(r'(^|[\s,])' + re.escape(f) + r'([\s,=]|$)', h, re.M) is not None
# Abschnitt zu --load-mode (bis zur nächsten Option)
m = re.search(r'--load-mode\b(.*?)(?=\n\s*-{1,2}[a-z]|\Z)', h, re.S)
block = m.group(1) if m else ""
def tok(v): return re.search(r'(?<![\w+-])' + re.escape(v) + r'(?![\w+-])', block) is not None
base = ["read", "load", "ram", "memory", "copy", "malloc", "alloc", "buffer", "host", "nommap", "no-mmap", "dio", "direct", "directio"]
# nur echte Aufzählungswerte ("- wert: …"), nicht Wörter aus Beschreibungen ("slower load")
vals = set(re.findall(r'^\s*-\s+([\w+]+):', block, re.M))
if m and "mlock" in vals and "mmap" in vals:
    print("--load-mode mlock"); sys.exit(0)   # mlock ohne mmap (eigener Prozessspeicher)
if m:
    for v in [b + "+mlock" for b in base] + base:
        if (v in vals if vals else tok(v)) and v not in ("dio", "direct", "directio"):
            print(f"--load-mode {v}"); sys.exit(0)
if has_flag("--no-mmap"):
    print("--no-mmap --mlock" if has_flag("--mlock") else "--no-mmap"); sys.exit(0)
sys.exit(3)
PY
) || {
    echo "render: kein Lademodus ohne mmap im Build gefunden. Auszug aus 'llama serve --help':" >&2
    grep -iE -A6 -- '--load-mode|mmap|mlock' <<<"$help" | head -30 >&2
    echo "Bitte explizit vorgeben, z.B. LOAD_ARGS='--load-mode <wert>'" >&2
    exit 3
  }
fi

unit=$(sed "/^#/!s|@LOAD_ARGS@|$LOAD_ARGS|" "$TPL")
# ab 3.6: gemessene Startwerte einsetzen (nur gesetzte Werte, nur Zahlen)
for _kv in "threads:${THREADS:-}" "threads-batch:${THREADS_BATCH:-}" "batch-size:${BATCH:-}" "ubatch-size:${UBATCH:-}"; do
  _f=${_kv%%:*}; _v=${_kv#*:}
  [[ -z "$_v" ]] && continue
  [[ "$_v" =~ ^[0-9]+$ ]] || { echo "render: ungültiger Wert für --$_f: '$_v'" >&2; exit 4; }
  unit=$(sed -E "s/^(  --$_f )[0-9]+ \\\\$/\\1$_v \\\\/" <<<"$unit")
done
# ab 3.7: Binary-Pfad und zusätzliche Flags (z.B. --no-repack) einsetzen; Flags werden unten mitgeprüft
if [[ "$BIN" != /home/chris/.local/bin/llama ]]; then
  unit=$(sed "s| /home/chris/.local/bin/llama serve | $BIN serve |" <<<"$unit")
  grep -qF -- " $BIN serve " <<<"$unit" || { echo "render: Binary-Pfad nicht ersetzt" >&2; exit 4; }
fi
# ab 3.7: NUMA-Variante (Standard: Gewichte verschränkt über beide Sockel, Threads verteilt)
if [[ -n "${NUMA_ARGS:-}" && "$NUMA_ARGS" != "--interleave=all" ]]; then
  [[ "$NUMA_ARGS" =~ ^--cpunodebind=[0-9]+([[:space:]]+--(preferred|membind)=[0-9]+)?$ ]] \
    || { echo "render: NUMA_ARGS '$NUMA_ARGS' ungültig" >&2; exit 4; }
  unit=$(sed "s|^ExecStart=/usr/bin/numactl --interleave=all |ExecStart=/usr/bin/numactl $NUMA_ARGS |" <<<"$unit")
  grep -qF -- "ExecStart=/usr/bin/numactl $NUMA_ARGS " <<<"$unit" || { echo "render: NUMA_ARGS nicht eingesetzt" >&2; exit 4; }
fi
if [[ -n "${LLAMA_NUMA:-}" && "$LLAMA_NUMA" != distribute ]]; then
  [[ "$LLAMA_NUMA" =~ ^(numactl|isolate)$ ]] || { echo "render: LLAMA_NUMA '$LLAMA_NUMA' ungültig" >&2; exit 4; }
  unit=$(sed -E "s/^(  --numa )distribute \\\\$/\\1$LLAMA_NUMA \\\\/" <<<"$unit")
  grep -qF -- "  --numa $LLAMA_NUMA \\" <<<"$unit" || { echo "render: --numa nicht eingesetzt" >&2; exit 4; }
fi
if [[ -n "${EXTRA_ARGS:-}" ]]; then
  [[ "$EXTRA_ARGS" =~ ^--[a-z0-9-]+([[:space:]]+[A-Za-z0-9._+/:=-]+)*$ ]] \
    || { echo "render: EXTRA_ARGS '$EXTRA_ARGS' ungültig" >&2; exit 4; }
  grep -qE '^  --no-webui$' <<<"$unit" || { echo "render: Vorlage endet nicht mit '  --no-webui'" >&2; exit 4; }
  unit=$(sed "s|^  --no-webui$|  --no-webui \\\\\n  $EXTRA_ARGS|" <<<"$unit")
fi
grep -q '@LOAD_ARGS@' <<<"$(grep -v '^#' <<<"$unit")" && { echo "render: Platzhalter nicht ersetzt" >&2; exit 4; }

missing=()
while read -r f; do
  grep -qE -- "(^|[[:space:],])${f}([[:space:],=]|$)" <<<"$help" || missing+=("$f")
done < <(sed -n '/^ExecStart=/,/^[^ ]/p' <<<"$unit" | sed -E 's#^ExecStart=/usr/bin/numactl( --[a-z]+=[a-z0-9,]+)+ ##' \
         | grep -oE '(^|[[:space:]])--[a-z0-9-]+' | tr -d ' ' | sort -u)
if [[ ${#missing[@]} -gt 0 ]]; then
  echo "render: unbekannte Flags für diesen Build: ${missing[*]}" >&2; exit 5
fi
# der gewählte Wert für --load-mode muss im Hilfetext vorkommen
if [[ "$LOAD_ARGS" == --load-mode* ]]; then
  v=${LOAD_ARGS#--load-mode }
  grep -qF -- "$v" <<<"$help" || { echo "render: Wert '$v' für --load-mode nicht in der Hilfe" >&2; exit 5; }
fi

echo "render: Lademodus '$LOAD_ARGS'" >&2
if [[ "$DST" == --check ]]; then exit 0; fi
tmp=$(mktemp "$(dirname "$DST")/.mag-llm.XXXXXX")
printf '%s\n' "$unit" > "$tmp"; chmod 0644 "$tmp"; mv -f "$tmp" "$DST"
