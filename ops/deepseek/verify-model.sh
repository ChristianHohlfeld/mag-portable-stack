#!/usr/bin/env bash
# Prüft die 5 GGUF-Teile von DeepSeek-V4-Flash-0731 UD-Q4_K_XL (manuelle Diagnose).
#   verify-model.sh --quick [DIR]   Existenz + exakte Größe, keine offenen Download-Dateien
#   verify-model.sh --full  [DIR]   zusätzlich SHA-256 (liest ~155 GB)
# Im Betrieb übernimmt das model-gate.sh (Timer), das nach bestandener Prüfung mag-llm startet.
set -euo pipefail
HERE=$(dirname "$(readlink -f "$0")")
MODE="${1:---quick}"
[[ -n "${2:-}" ]] && MODEL_DIR="$2"
# shellcheck source=model-manifest.sh
source "${MAG_MODEL_MANIFEST:-$HERE/model-manifest.sh}"
fail=0
for p in "${MODEL_PARTS[@]}"; do
  f=$(model_part "$p")
  if [[ ! -f "$f" ]]; then echo "FEHLT: $f" >&2; fail=1; continue; fi
  sz=$(stat -Lc %s "$f")
  if [[ "$sz" != "${MODEL_SIZE[$p]}" ]]; then
    echo "GRÖSSE: $f ist $sz Bytes, erwartet ${MODEL_SIZE[$p]} (Download unvollständig?)" >&2; fail=1; continue
  fi
  for side in "$f.part" "$f.aria2" "$f.incomplete" "$f.lock"; do
    if [[ -e "$side" ]]; then echo "DOWNLOAD AKTIV: $side" >&2; fail=1; fi
  done
  if [[ "$MODE" == "--full" ]]; then
    got=$(sha256sum "$f" | cut -d' ' -f1)
    if [[ "$got" != "${MODEL_SHA[$p]}" ]]; then echo "SHA FALSCH: $f -> $got" >&2; fail=1
    else echo "OK $p $got" >&2; fi
  fi
done
[[ $fail -eq 0 ]] || { echo "verify-model: NICHT OK" >&2; exit 1; }
echo "verify-model: OK ($MODE)" >&2
