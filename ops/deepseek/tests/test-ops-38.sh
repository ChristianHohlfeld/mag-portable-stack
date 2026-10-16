#!/usr/bin/env bash
# Offline-Tests für Runde 3.8 (ohne root, llama, systemd). Aufruf aus dem Repo-Wurzelverzeichnis.
set -euo pipefail
ROOT=$(cd "$(dirname "$(readlink -f "$0")")/../../.." && pwd); cd "$ROOT"
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
ok() { echo "ok  $*"; }; fail() { echo "FEHLER $*"; exit 1; }
NEW=(ops/deepseek/build-ik.sh ops/deepseek/render-ik-unit.sh ops/deepseek/upgrade-3.8.sh ops/deepseek/llm-config.sh
     ops/deepseek/render-llm-unit.sh)
bash -n "${NEW[@]}" && ok "bash -n (3.8)"
bad=$(grep -nE '\| *grep -q' "${NEW[@]}" || true)
[[ -z "$bad" ]] && ok "keine pipefail-anfälligen grep -q-Pipelines" || fail "$bad"

# Hilfetext eines ik-Binarys nachbilden (alle Flags, die render-ik-unit.sh setzt)
IKF="--model --alias --host --port --ctx-size --parallel --threads --threads-batch --numa --batch-size --ubatch-size
     --mlock --flash-attn --cache-type-k --cache-type-v --cache-ram --jinja --chat-template-file --reasoning-format
     --temp --top-p --min-p --metrics --webui -rtr -muge"
for f in $IKF; do echo "  $f VALUE"; done > "$T/ikhelp"
grep -v -- '-muge' "$T/ikhelp" > "$T/ikhelp-ohne-muge"
fake=$T/llama-server; printf '#!/bin/sh\n' > "$fake"; chmod +x "$fake"
base=(HELP_FILE=$T/ikhelp IK_BIN=$fake THREADS=16 THREADS_BATCH=28 BATCH=1024 UBATCH=1024)

# 1) Unit für ik: ExecStart ersetzt, Rest der Vorlage wörtlich
env "${base[@]}" IK_ENV="MAG_NUMA_LOCAL=16 MAG_THP=1" IK_ARGS=-muge bash ops/deepseek/render-ik-unit.sh ops/mag-llm.service "$T/u1" 2>/dev/null
grep -qF -- "ExecStart=/usr/bin/numactl --interleave=all $fake \\" "$T/u1" || fail "ExecStart ohne ik-Binary"
for w in "  --parallel 1 \\" "  --cache-type-k f16 \\" "  --cache-type-v f16 \\" "  -rtr \\" "  -muge" "  --threads 16 \\" \
         "  --threads-batch 28 \\" "  --mlock \\" "  --webui none \\" "Environment=MAG_NUMA_LOCAL=16" "Environment=MAG_THP=1" \
         "  --model /home/chris/models/deepseek-v4/DeepSeek-V4-Flash-0731-UD-Q4_K_XL-00001-of-00005.gguf \\" \
         "  --chat-template-file /home/chris/llama-templates/deepseek-ai-DeepSeek-V4-Flash-0731.jinja \\" "  --alias deepseek-v4-flash-0731 \\"; do
  grep -qF -- "$w" "$T/u1" || fail "ik-Unit: '$w' fehlt"
done
! grep -qE -- '--cache-type-[kv] +(q|iq)' "$T/u1" || fail "quantisierter KV-Cache (V4-Bug #25382)"
strip_exec() { sed '/^ExecStart=/,/[^\\]$/d' "$1" | grep -vE '^(Description=|Environment=MAG_|# ik_llama.cpp \(ab 3.8)'; }
diff <(strip_exec ops/mag-llm.service) <(strip_exec "$T/u1") >/dev/null || { diff <(strip_exec ops/mag-llm.service) <(strip_exec "$T/u1") | head; fail "ik-Unit weicht außerhalb von ExecStart ab"; }
python3 ops/check-unit-env.py "$T" >/dev/null || fail "Environment-Zeilen der ik-Unit"
ok "render-ik: ExecStart ik, -rtr, f16-KV, --parallel 1, Varianten-Env, Rest der Vorlage unverändert"
# 2) ohne -rtr (mmap-Variante), keine Env-Zeilen
env "${base[@]}" IK_RTR=0 bash ops/deepseek/render-ik-unit.sh ops/mag-llm.service "$T/u2" 2>/dev/null
! grep -qE -- '^  -rtr' "$T/u2" && ! grep -q '^Environment=MAG_' "$T/u2" || fail "IK_RTR=0 / leeres IK_ENV"
ok "render-ik: IK_RTR=0 ohne -rtr"
# 3) Abweisungen
if env "${base[@]}" HELP_FILE=$T/ikhelp-ohne-muge IK_ARGS=-muge bash ops/deepseek/render-ik-unit.sh ops/mag-llm.service "$T/u3" 2>/dev/null; then fail "unbekanntes Flag angenommen"; fi
if env "${base[@]}" IK_ENV="LD_PRELOAD=/tmp/x.so" bash ops/deepseek/render-ik-unit.sh ops/mag-llm.service "$T/u4" 2>/dev/null; then fail "fremde Env-Variable angenommen"; fi
if env "${base[@]}" IK_ARGS='-muge; rm -rf /' bash ops/deepseek/render-ik-unit.sh ops/mag-llm.service "$T/u5" 2>/dev/null; then fail "IK_ARGS-Injektion angenommen"; fi
if env "${base[@]}" IK_BIN='/tmp/x y' bash ops/deepseek/render-ik-unit.sh ops/mag-llm.service "$T/u6" 2>/dev/null; then fail "IK_BIN mit Leerzeichen angenommen"; fi
if env "${base[@]}" THREADS=16x bash ops/deepseek/render-ik-unit.sh ops/mag-llm.service "$T/u7" 2>/dev/null; then fail "ungültige Threads angenommen"; fi
[[ ! -e "$T/u3" && ! -e "$T/u4" && ! -e "$T/u5" ]] || fail "bei Abbruch wurde trotzdem geschrieben"
ok "render-ik: unbekannte Flags, fremde Env, Injektion, ungültige Werte -> Abbruch, nichts geschrieben"

# 4) llm-config: ENGINE=ik speichern/laden/rendern, "-" löscht, Standard bleibt llama.cpp
flags=$(sed -n '/^ExecStart=/,/^[^ ]/p' ops/mag-llm.service | grep -oE '(^|[[:space:]])--[a-z0-9-]+' | tr -d ' ' | sort -u)
{ for f in $flags; do echo "  $f VALUE"; done; echo "  -lm, --load-mode MODE   mmap, mmap+mlock, mlock"; } > "$T/llhelp"
(
  export MAG_LLM_ENV=$T/cfg/llm.env
  source ops/deepseek/llm-config.sh
  ENGINE=ik IK_BIN=$fake IK_ENV="MAG_NUMA_LOCAL=16" THREADS=16 THREADS_BATCH=28 llm_config_load; llm_config_save
  grep -q '^ENGINE=ik$' "$MAG_LLM_ENV" && grep -q "^IK_BIN=$fake$" "$MAG_LLM_ENV" && grep -q '^IK_ENV="MAG_NUMA_LOCAL=16"$' "$MAG_LLM_ENV" \
    || fail "llm.env ohne ENGINE/IK_BIN/IK_ENV"
  unset ENGINE IK_BIN IK_ENV THREADS THREADS_BATCH; llm_config_load
  [[ $ENGINE == ik && $IK_BIN == "$fake" && $IK_ENV == MAG_NUMA_LOCAL=16 && $IK_RTR == 1 && $THREADS == 16 ]] || fail "Roundtrip ik"
  HELP_FILE=$T/ikhelp render_unit "$T/u8" 2>/dev/null
  grep -qF -- "numactl --interleave=all $fake \\" "$T/u8" && grep -q '^Environment=MAG_NUMA_LOCAL=16$' "$T/u8" || fail "render_unit ENGINE=ik"
  # render-llm-unit.sh (Aufrufer aus 3.5–3.7) liest ENGINE=ik aus llm.env und delegiert
  HELP_FILE=$T/ikhelp bash ops/deepseek/render-llm-unit.sh ops/mag-llm.service "$T/u9" 2>/dev/null || fail "render-llm-unit delegiert nicht"
  grep -qF -- " $fake \\" "$T/u9" || fail "render-llm-unit: ik-Unit erwartet"
  ENGINE=- IK_BIN=- IK_ENV=- llm_config_load; llm_config_save
  ! grep -qE '^(ENGINE|IK_BIN|IK_ENV)=' "$MAG_LLM_ENV" || fail "'-' löscht nicht"
  unset ENGINE IK_BIN IK_ENV; llm_config_load
  [[ $ENGINE == llama ]] || fail "Standard-Engine"
  HELP_FILE=$T/llhelp render_unit "$T/u10" 2>/dev/null
  grep -qF -- " /home/chris/.local/bin/llama serve " "$T/u10" || fail "llama.cpp-Unit nach dem Löschen"
  if ENGINE=ik IK_BIN= llm_config_load 2>/dev/null; then fail "ENGINE=ik ohne IK_BIN angenommen"; fi
  if ENGINE=vllm llm_config_load 2>/dev/null; then fail "unbekannte Engine angenommen"; fi
) || fail "llm-config"
ok "llm-config: ENGINE=ik speichern, laden, rendern, delegieren, löschen; Standard llama.cpp"

# 5) build-ik: fester Stand, nativ, Symbole, Patches, DeepSeek-V4-Prüfung
grep -qE '^REF=\$\{IK_REF:-[0-9a-f]{40}\}' ops/deepseek/build-ik.sh || fail "build-ik: kein fest gepinnter Commit"
grep -q 'GGML_NATIVE=ON' ops/deepseek/build-ik.sh && grep -q -- '-DCMAKE_C_FLAGS=-g' ops/deepseek/build-ik.sh \
  && grep -q 'LLM_ARCH_DEEPSEEK4' ops/deepseek/build-ik.sh || fail "build-ik: nativ/Symbole/V4-Prüfung"
ok "build-ik: gepinnter Commit, GGML_NATIVE, mit Symbolen, prüft DeepSeek V4"
# 6) ik-Patches: nur die erwarteten Dateien
p1=ops/deepseek/ik-patches/0001-server-prompt-progress.patch; p2=ops/deepseek/ik-patches/0002-numa-local.patch
[[ -f $p1 && -f $p2 ]] || fail "ik-Patches fehlen"
f1=$(sed -n 's|^diff --git a/\([^ ]*\) b/.*|\1|p' $p1 | sort -u | tr '\n' ' ')
f2=$(sed -n 's|^diff --git a/\([^ ]*\) b/.*|\1|p' $p2 | sort -u | tr '\n' ' ')
[[ "$f1" == "examples/server/server-context.cpp examples/server/server-context.h examples/server/server-task.cpp examples/server/server-task.h " ]] || fail "Patch 0001 berührt: $f1"
[[ "$f2" == "ggml/src/ggml.c src/llama.cpp " ]] || fail "Patch 0002 berührt: $f2"
grep -q 'MAG_NUMA_LOCAL' $p2 && grep -q 'SYS_mbind' $p2 && grep -q 'SYS_move_pages' $p2 && grep -q '!mag_numa_local()' $p2 \
  || fail "Patch 0002 unvollständig"
ok "ik-Patches: 0001 nur Server (prompt_progress), 0002 nur ggml.c/llama.cpp (NUMA-lokal, abschaltbar)"
# 7) upgrade-3.8: Sicherheitsnetz
u=ops/deepseek/upgrade-3.8.sh
for w in 'mag-model-pin.service' 'preflight-tests.py' 'restore 1' '>= 1.05' 'tg\[n\] >= a_tg' 'sanity' 'restore 0'; do
  grep -qE -- "$w" "$u" || fail "upgrade-3.8: '$w' fehlt"
done
ok "upgrade-3.8: Pin-Pflicht, Schnelltest, Go/No-Go mit Rollback, nur übernehmen wenn >= 5 % besser und tg nicht schlechter"
echo "== 3.8-Tests grün"
