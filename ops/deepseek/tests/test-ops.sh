#!/usr/bin/env bash
# Offline-Tests der Betriebsskripte (ohne root, ohne llama, ohne systemd). Aufruf aus dem Repo-Wurzelverzeichnis.
set -euo pipefail
ROOT=$(cd "$(dirname "$(readlink -f "$0")")/../../.." && pwd); cd "$ROOT"
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
ok() { echo "ok  $*"; }; fail() { echo "FEHLER $*"; exit 1; }

# 1) Syntax aller Skripte
bash -n ops/*.sh ops/deepseek/*.sh ops/audit/*.sh ops/security/*.sh ops/deepseek/tests/*.sh && ok "bash -n"
python3 -I -m py_compile ops/check-unit-env.py && ok "py_compile"

# 2) Environment-Zeilen
python3 -I ops/check-unit-env.py ops >/dev/null && ok "Environment-Zeilen im Repo"
mkdir "$T/bad"; printf '[Service]\nEnvironment=A=b c\n' > "$T/bad/x.conf"
if python3 -I ops/check-unit-env.py "$T/bad" >/dev/null; then fail "check-unit-env erkennt Leerzeichen nicht"; else ok "check-unit-env erkennt Leerzeichen"; fi

# 3) Unit-Rendering gegen verschiedene Hilfetexte
flags=$(sed -n '/^ExecStart=/,/^[^ ]/p' ops/mag-llm.service | grep -oE '(^|[[:space:]])--[a-z0-9-]+' | tr -d ' ' | sort -u)
base() { for f in $flags; do echo "  $f VALUE   text"; done; }
{ base; echo "  --load-mode MODE   mmap, mmap+mlock, read, read+mlock (default: mmap)"; echo "  --zzz  x"; } > "$T/h1"
{ base; echo "  --no-mmap   do not memory-map"; echo "  --mlock   lock"; } > "$T/h2"
{ base; echo "  --load-mode MODE   mmap|mmap+mlock"; } > "$T/h3"
{ for f in $flags; do [[ $f == --ubatch-size ]] || echo "  $f VALUE"; done; echo "  --load-mode M  read+mlock"; } > "$T/h4"
HELP_FILE=$T/h1 bash ops/deepseek/render-llm-unit.sh ops/mag-llm.service "$T/u1" 2>/dev/null
grep -q -- '--load-mode read+mlock \\' "$T/u1" && ! grep -v '^#' "$T/u1" | grep -q '@LOAD_ARGS@' && ok "render: --load-mode read+mlock"
HELP_FILE=$T/h2 bash ops/deepseek/render-llm-unit.sh ops/mag-llm.service "$T/u2" 2>/dev/null
grep -q -- '--no-mmap --mlock \\' "$T/u2" && ok "render: --no-mmap --mlock"
if HELP_FILE=$T/h3 bash ops/deepseek/render-llm-unit.sh ops/mag-llm.service "$T/u3" 2>/dev/null; then fail "render ohne no-mmap-Modus darf nicht schreiben"; fi
[[ ! -e "$T/u3" ]] && ok "render: Abbruch ohne Lademodus, nichts geschrieben"
if HELP_FILE=$T/h4 bash ops/deepseek/render-llm-unit.sh ops/mag-llm.service "$T/u4" 2>/dev/null; then fail "unbekanntes Flag nicht erkannt"; fi
ok "render: Abbruch bei unbekanntem Flag"
HELP_FILE=$T/h3 LOAD_ARGS='--load-mode mmap+mlock' bash ops/deepseek/render-llm-unit.sh ops/mag-llm.service "$T/u5" 2>/dev/null
grep -q -- '--load-mode mmap+mlock \\' "$T/u5" && ok "render: LOAD_ARGS-Vorgabe"

# 4) Unit-Inhalt: Startwerte, kein Pin, kein drop_caches, Postload nicht blockierend
for want in '--threads 28 ' '--threads-batch 28 ' '--batch-size 2048 ' '--ubatch-size 2048 ' 'numactl --interleave=all' \
            '--numa distribute' 'MemorySwapMax=0' 'LimitMEMLOCK=infinity' 'ExecStartPost=-+/usr/bin/systemd-run --no-block'; do
  grep -qF -- "$want" "$T/u1" || fail "Unit ohne '$want'"
done
grep -qE '^(Wants|After)=.*mag-model-pin' "$T/u1" && fail "Unit hängt noch an mag-model-pin"
grep -qE '^Exec.*drop_caches' "$T/u1" && fail "drop_caches in der Unit"
grep -q "vmtouch -q -l" ops/mag-model-pin.service || fail "mag-model-pin.service fehlt oder sperrt nicht (vmtouch -l)"
grep -qE "^(BindsTo|PartOf|Requires)=.*mag-llm" ops/mag-model-pin.service && fail "Pin darf nicht an mag-llm hängen"
ok "Unit-Inhalt"
if command -v systemd-analyze >/dev/null; then
  mkdir -p "$T/v"; cp "$T/u1" "$T/v/mag-llm.service"
  out=$(systemd-analyze verify "$T/v/mag-llm.service" 2>&1 | grep -E 'Unknown (key|section)|Invalid|Failed to parse|bad-setting' || true)
  [[ -z "$out" ]] && ok "systemd-analyze verify (Syntax)" || fail "systemd-analyze: $out"
fi

# 4b) keine "… | grep -q"-Pipelines (unter pipefail bricht der Erzeuger per SIGPIPE ab -> falsches Ergebnis)
bad=$(grep -nE '\| *grep -q' ops/*.sh ops/deepseek/*.sh ops/audit/*.sh ops/security/*.sh 2>/dev/null || true)
[[ -z "$bad" ]] && ok "keine pipefail-anfälligen grep -q-Pipelines" || fail "grep -q in Pipeline: $bad"

# 5) Keine Secrets im Repo (nur verfolgte Dateien)
pat='-----BEGIN [A-Z ]*PRIVATE KEY-----|ghp_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|sk-[A-Za-z0-9]{32,}|xox[abp]-[A-Za-z0-9-]{20,}|AKIA[0-9A-Z]{16}|hf_[A-Za-z0-9]{30,}|BSA[A-Za-z0-9_-]{25,}'
hits=$(git ls-files -z | xargs -0 grep -IlE -- "$pat" 2>/dev/null || true)
[[ -z "$hits" ]] && ok "keine Secrets in verfolgten Dateien" || fail "mögliche Secrets: $hits"
echo "== ops-Tests grün"
