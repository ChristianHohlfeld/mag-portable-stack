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
if m:
    for v in [b + "+mlock" for b in base] + base:
        if tok(v):
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
grep -q '@LOAD_ARGS@' <<<"$(grep -v '^#' <<<"$unit")" && { echo "render: Platzhalter nicht ersetzt" >&2; exit 4; }

missing=()
while read -r f; do
  grep -qE -- "(^|[[:space:],])${f}([[:space:],=]|$)" <<<"$help" || missing+=("$f")
done < <(sed -n '/^ExecStart=/,/^[^ ]/p' <<<"$unit" | sed 's|^ExecStart=/usr/bin/numactl --interleave=all ||' \
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
