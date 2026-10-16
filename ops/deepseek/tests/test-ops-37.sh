#!/usr/bin/env bash
# Offline-Tests für Runde 3.7 (ohne root, llama, systemd). Aufruf aus dem Repo-Wurzelverzeichnis.
set -euo pipefail
ROOT=$(cd "$(dirname "$(readlink -f "$0")")/../../.." && pwd); cd "$ROOT"
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
ok() { echo "ok  $*"; }; fail() { echo "FEHLER $*"; exit 1; }
NEW=(ops/deepseek/build-llama.sh ops/deepseek/memdiag.sh ops/deepseek/upgrade-3.7.sh ops/deepseek/profile.sh
     ops/deepseek/http-bench.sh ops/deepseek/render-llm-unit.sh ops/deepseek/llm-config.sh)
bash -n "${NEW[@]}" && ok "bash -n (3.7)"
bad=$(grep -nE '\| *grep -q' "${NEW[@]}" || true)
[[ -z "$bad" ]] && ok "keine pipefail-anfälligen grep -q-Pipelines" || fail "$bad"
grep -q 'wait_gone' ops/deepseek/profile.sh && ok "profile.sh wartet, bis die Last-Anfrage fertig ist"

flags=$(sed -n '/^ExecStart=/,/^[^ ]/p' ops/mag-llm.service | grep -oE '(^|[[:space:]])--[a-z0-9-]+' | tr -d ' ' | sort -u)
{ for f in $flags; do echo "  $f VALUE"; done; echo "  -lm, --load-mode MODE   mmap, mmap+mlock, mlock"; echo "  --repack, -nr, --no-repack   repack"; } > "$T/help"
{ for f in $flags; do echo "  $f VALUE"; done; echo "  -lm, --load-mode MODE   mmap, mmap+mlock, mlock"; } > "$T/help-norepack-unbekannt"
fake=$T/llama; printf '#!/bin/sh\n' > "$fake"; chmod +x "$fake"

# 1) eigenes Binary + Zusatzflags
HELP_FILE=$T/help LLAMA_BIN=$fake EXTRA_ARGS=--no-repack LOAD_ARGS="--load-mode mmap+mlock" MAG_LLM_ENV=/nonexistent \
  bash ops/deepseek/render-llm-unit.sh ops/mag-llm.service "$T/u1" 2>/dev/null
grep -qF -- "--interleave=all $fake serve \\" "$T/u1" || fail "Binary-Pfad nicht eingesetzt"
grep -qE '^  --no-webui \\$' "$T/u1" && grep -qE '^  --no-repack$' "$T/u1" || fail "--no-repack nicht angehängt"
ok "render: LLAMA_BIN und EXTRA_ARGS"
# 2) unbekanntes Zusatzflag -> Abbruch
if HELP_FILE=$T/help-norepack-unbekannt EXTRA_ARGS=--no-repack LOAD_ARGS="--load-mode mmap+mlock" MAG_LLM_ENV=/nonexistent \
   bash ops/deepseek/render-llm-unit.sh ops/mag-llm.service "$T/u2" 2>/dev/null; then fail "unbekanntes Zusatzflag angenommen"; fi
ok "render: unbekanntes Zusatzflag -> Abbruch"
# 3) Injektion über EXTRA_ARGS / LLAMA_BIN abgewiesen
if HELP_FILE=$T/help EXTRA_ARGS='--no-repack; rm -rf /' LOAD_ARGS="--load-mode mmap+mlock" MAG_LLM_ENV=/nonexistent \
   bash ops/deepseek/render-llm-unit.sh ops/mag-llm.service "$T/u3" 2>/dev/null; then fail "EXTRA_ARGS-Injektion angenommen"; fi
if HELP_FILE=$T/help LLAMA_BIN='/tmp/x y' LOAD_ARGS="--load-mode mmap+mlock" MAG_LLM_ENV=/nonexistent \
   bash ops/deepseek/render-llm-unit.sh ops/mag-llm.service "$T/u4" 2>/dev/null; then fail "LLAMA_BIN mit Leerzeichen angenommen"; fi
ok "render: ungültige EXTRA_ARGS/LLAMA_BIN abgewiesen"
# 4) llm-config: speichern, laden, mit "-" löschen
(
  export MAG_LLM_ENV=$T/cfg/llm.env HELP_FILE=$T/help
  source ops/deepseek/llm-config.sh
  LLAMA_BIN=$fake EXTRA_ARGS=--no-repack llm_config_load; llm_config_save
  grep -q "^LLAMA_BIN=$fake$" "$MAG_LLM_ENV" && grep -q '^EXTRA_ARGS="--no-repack"$' "$MAG_LLM_ENV" || fail "llm.env ohne LLAMA_BIN/EXTRA_ARGS"
  unset LLAMA_BIN EXTRA_ARGS; llm_config_load
  [[ $LLAMA_BIN == "$fake" && $EXTRA_ARGS == --no-repack ]] || fail "Roundtrip"
  render_unit "$T/u5" 2>/dev/null; grep -qF -- " $fake serve " "$T/u5" || fail "render_unit mit LLAMA_BIN"
  LLAMA_BIN=- EXTRA_ARGS=- llm_config_load; llm_config_save
  ! grep -qE '^(LLAMA_BIN|EXTRA_ARGS)=' "$MAG_LLM_ENV" || fail "'-' löscht nicht"
  unset LLAMA_BIN EXTRA_ARGS; llm_config_load; render_unit "$T/u6" 2>/dev/null
  grep -qF -- " /home/chris/.local/bin/llama serve " "$T/u6" || fail "Standard-Binary nach dem Löschen"
) || fail "llm-config"
ok "llm-config: LLAMA_BIN/EXTRA_ARGS speichern, laden, löschen"
# 5) build-llama verlangt den F16C-Fix
grep -q 'merge-base --is-ancestor 685f70894' ops/deepseek/build-llama.sh && grep -q 'GGML_NATIVE=ON' ops/deepseek/build-llama.sh \
  && grep -q -- '-DCMAKE_C_FLAGS=-g' ops/deepseek/build-llama.sh && ok "build-llama: F16C-Fix Pflicht, nativ, mit Symbolen"
# 6) bekannte V4-Bugs: quantisierter K-Cache (#25382, Unsinn) und Prompt-Cache ohne -np 1 (#25259)
grep -qE -- '^  --parallel 1 ' ops/mag-llm.service || fail "--parallel 1 fehlt (llama.cpp #25259)"
! grep -qE -- '--cache-type-[kv] +(q|iq)' ops/mag-llm.service || fail "quantisierter KV-Cache gesetzt (llama.cpp #25382)"
ok "Unit: --parallel 1, KV-Cache nicht quantisiert (V4-Bugs #25259/#25382)"
# 7) llama.cpp-Patch + Selbsttest vorhanden, Patch ändert nur ggml-cpu
p=$(ls ops/deepseek/llama-patches/*.patch)
[[ -n "$p" && -f ops/deepseek/llama-patches/qcheck-q8_0-repack.cpp ]] || fail "llama-patches unvollständig"
files=$(sed -n 's|^diff --git a/\([^ ]*\) b/.*|\1|p' $p | sort -u | tr '\n' ' ')
[[ "$files" == "ggml/src/ggml-cpu/arch-fallback.h ggml/src/ggml-cpu/arch/x86/repack.cpp ggml/src/ggml-cpu/repack.cpp " ]] || fail "Patch berührt: $files"
grep -q 'MAG_NO_Q8_0_REPACK' $p && ok "llama.cpp-Patch: nur ggml-cpu, abschaltbar per MAG_NO_Q8_0_REPACK"
echo "== 3.7-Tests grün"
