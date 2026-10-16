#!/usr/bin/env bash
# DeepSeek-V4-Flash-0731 als mag-llm installieren – parallel zum Download, OHNE Modellstart.
# mag-llm startet automatisch, sobald mag-model-gate die SHA-256-Vollprüfung bestanden hat.
#
#   sudo bash ops/deepseek/install-deepseek.sh            Trockenlauf
#   sudo bash ops/deepseek/install-deepseek.sh --apply    ausführen
set -euo pipefail
SRC=$(cd "$(dirname "$(readlink -f "$0")")/../.." && pwd)
CHRIS_HOME=/home/chris
BIN=$CHRIS_HOME/.local/bin/llama                 # Unterbefehl "serve"
TPL_DIR=$CHRIS_HOME/llama-templates
TPL=$TPL_DIR/deepseek-ai-DeepSeek-V4-Flash-0731.jinja
BACKUP=$CHRIS_HOME/mag-infra/systemd
LIB=/usr/local/lib/mag-deepseek
MIN_BUILD=10254   # erster Build mit 0731-Template + DSML-Parser-Fix (llama.cpp PR #26398)
APPLY=0; [[ "${1:-}" == "--apply" ]] && APPLY=1
run() { echo "+ $*"; if [[ $APPLY -eq 1 ]]; then "$@"; fi; }
[[ $EUID -eq 0 ]] || { echo "bitte mit sudo"; exit 1; }
ts=$(date +%Y%m%d%H%M)
UNIT="$SRC/ops/mag-llm.service"
for f in "$UNIT" "$SRC/ops/mag-model-gate.service" "$SRC/ops/mag-model-gate.timer" \
         "$SRC/ops/deepseek/model-gate.sh" "$SRC/ops/deepseek/model-manifest.sh" "$SRC/ops/deepseek/verify-model.sh"; do
  [[ -f "$f" ]] || { echo "fehlt: $f"; exit 1; }
done

echo "== 1. llama.cpp-Build"
[[ -x "$BIN" ]] || { echo "   FEHLER: $BIN fehlt"; exit 1; }
ver=$(runuser -u chris -- "$BIN" --version 2>&1 | head -5 || true)    # "version: 0.6.0-dev (build 11630, commit 6868a52cb)"
build=$(sed -nE 's/.*build ([0-9]+), commit ([0-9a-f]+).*/\1/p' <<<"$ver" | head -1)
commit=$(sed -nE 's/.*build ([0-9]+), commit ([0-9a-f]+).*/\2/p' <<<"$ver" | head -1)
[[ -n "$build" && -n "$commit" ]] || { echo "   FEHLER: Version nicht erkannt:"; sed 's/^/     /' <<<"$ver"; exit 1; }
echo "   build=$build commit=$commit"
[[ $build -ge $MIN_BUILD ]] || { echo "   FEHLER: Build < $MIN_BUILD"; exit 1; }

echo "== 2. Flags der Unit gegen 'llama serve --help'"
help=$(runuser -u chris -- "$BIN" serve --help 2>&1 || true)
missing=0
while read -r f; do
  if grep -qE -- "(^|[[:space:],])${f}([[:space:],=]|$)" <<<"$help"; then echo "   ok    $f"; else echo "   FEHLT $f"; missing=1; fi
done < <(sed -n '/^ExecStart=/,/^[^ ]/p' "$UNIT" | grep -oE '(^|[[:space:]])--[a-z0-9-]+' | tr -d ' ' | sort -u)
[[ $missing -eq 0 ]] || { echo "   FEHLER: unbekannte Flags – ops/mag-llm.service anpassen"; exit 1; }
grep -q 'mmap+mlock' <<<"$help" || echo "   WARNUNG: --help nennt 'mmap+mlock' nicht wörtlich – Ausgabe von --load-mode prüfen"

echo "== 3. Chat-Template vom Build-Commit $commit"
url="https://raw.githubusercontent.com/ggml-org/llama.cpp/${commit}/models/templates/deepseek-ai-DeepSeek-V4-Flash-0731.jinja"
if [[ $APPLY -eq 1 ]]; then
  runuser -u chris -- install -d "$TPL_DIR"
  runuser -u chris -- curl -fsSL --connect-timeout 10 --max-time 300 "$url" -o "$TPL.new"
  grep -q 'DSML' "$TPL.new" || { echo "   FEHLER: Template ohne DSML"; exit 1; }
  runuser -u chris -- mv -f "$TPL.new" "$TPL"; echo "   $TPL ($(wc -c <"$TPL") Bytes)"
else
  echo "+ lade $url -> $TPL"
fi

echo "== 4. Gate-Skripte nach $LIB, Status nach /var/lib/mag-llm"
run install -d -m 0755 "$LIB" /var/lib/mag-llm
run install -m 0755 "$SRC/ops/deepseek/model-gate.sh" "$SRC/ops/deepseek/verify-model.sh" "$LIB/"
run install -m 0644 "$SRC/ops/deepseek/model-manifest.sh" "$LIB/"

echo "== 5. Provisorischen deepseek.service zurückbauen"
run install -d -o chris -g chris "$BACKUP"
if [[ -e /etc/systemd/system/deepseek.service ]]; then
  run cp -a /etc/systemd/system/deepseek.service "$BACKUP/deepseek.service.bak-$ts"
  run systemctl disable --now deepseek.service
  run rm -f /etc/systemd/system/deepseek.service
fi

echo "== 6. mag-llm.service (DeepSeek, :8090, Start erst nach Freigabe) + Gate-Timer"
if [[ -e /etc/systemd/system/mag-llm.service ]]; then
  run cp -aL /etc/systemd/system/mag-llm.service "$BACKUP/mag-llm.service.bak-$ts"
  run rm -f /etc/systemd/system/mag-llm.service
fi
run install -m 0644 "$UNIT" /etc/systemd/system/mag-llm.service
run install -m 0644 "$SRC/ops/mag-model-gate.service" "$SRC/ops/mag-model-gate.timer" /etc/systemd/system/
run systemctl daemon-reload
run systemctl enable mag-llm.service
run systemctl enable --now mag-model-gate.timer
if [[ $APPLY -eq 1 ]]; then
  systemctl start --no-block mag-model-gate.service
  sleep 2; "$LIB/model-gate.sh" status || true
fi
echo "== 7. Warm halten (mag-model-pin, kein drop_caches, keine Neustarts von außen)"
if [[ $APPLY -eq 1 ]]; then bash "$SRC/ops/deepseek/warm.sh" install --apply; else bash "$SRC/ops/deepseek/warm.sh" install; fi
echo
echo "mag-llm startet automatisch nach bestandener Prüfung. Verlauf: journalctl -fu mag-model-gate -u mag-llm"
[[ $APPLY -eq 0 ]] && echo "(Trockenlauf – mit --apply ausführen)"
exit 0
