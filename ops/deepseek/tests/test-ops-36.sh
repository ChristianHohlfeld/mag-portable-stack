#!/usr/bin/env bash
# mag-test-timeout: 180
if [ -z "${MAG_TEST_DEADLINE_ARMED:-}" ]; then
  export MAG_TEST_DEADLINE_ARMED=1
  exec timeout --kill-after=5s 180s bash "$0" "$@"
fi
# Offline-Tests für Runde 3.6 (ohne root, llama, systemd). Aufruf aus dem Repo-Wurzelverzeichnis.
set -euo pipefail
ROOT=$(cd "$(dirname "$(readlink -f "$0")")/../../.." && pwd); cd "$ROOT"
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
ok() { echo "ok  $*"; }; fail() { echo "FEHLER $*"; exit 1; }
NEW=(ops/deepseek/llm-config.sh ops/deepseek/tune.sh ops/deepseek/profile.sh ops/deepseek/upgrade-3.6.sh ops/deepseek/render-llm-unit.sh)

bash -n "${NEW[@]}" && ok "bash -n (3.6)"
bad=$(grep -nE '\| *grep -q' "${NEW[@]}" || true)
[[ -z "$bad" ]] && ok "keine pipefail-anfälligen grep -q-Pipelines" || fail "$bad"

flags=$(sed -n '/^ExecStart=/,/^[^ ]/p' ops/mag-llm.service | grep -oE '(^|[[:space:]])--[a-z0-9-]+' | tr -d ' ' | sort -u)
{ for f in $flags; do echo "  $f VALUE   text"; done; echo "  --load-mode MODE   mmap, mmap+mlock, mlock (default: mmap)"; } > "$T/help"

# 1) Startwerte aus llm.env, Umgebung hat Vorrang
printf 'LOAD_ARGS="--load-mode mmap+mlock"\nTHREADS=24\nTHREADS_BATCH=56\nBATCH=2048\nUBATCH=512\n' > "$T/llm.env"
HELP_FILE=$T/help MAG_LLM_ENV=$T/llm.env bash ops/deepseek/render-llm-unit.sh ops/mag-llm.service "$T/u1" 2>/dev/null
for w in '--load-mode mmap+mlock \' '--threads 24 \' '--threads-batch 56 \' '--batch-size 2048 \' '--ubatch-size 512 \'; do
  grep -qF -- "$w" "$T/u1" || fail "u1 ohne '$w'"
done
ok "render: Werte aus llm.env"
HELP_FILE=$T/help MAG_LLM_ENV=$T/llm.env THREADS=20 bash ops/deepseek/render-llm-unit.sh ops/mag-llm.service "$T/u2" 2>/dev/null
grep -qF -- '--threads 20 \' "$T/u2" && grep -qF -- '--threads-batch 56 \' "$T/u2" && ok "render: Umgebung vor llm.env"
if HELP_FILE=$T/help MAG_LLM_ENV=$T/llm.env THREADS=abc bash ops/deepseek/render-llm-unit.sh ops/mag-llm.service "$T/u3" 2>/dev/null; then fail "ungültiger Wert angenommen"; fi
[[ ! -e "$T/u3" ]] && ok "render: ungültiger Wert -> Abbruch, nichts geschrieben"

# 2) llm-config: laden/speichern/render_unit
(
  export MAG_LLM_ENV=$T/cfg/llm.env HELP_FILE=$T/help
  # shellcheck source=../llm-config.sh
  source ops/deepseek/llm-config.sh
  llm_config_load
  [[ "$LOAD_ARGS" == "--load-mode mmap+mlock" && $THREADS == 28 && $UBATCH == 1024 ]] || fail "Standardwerte"
  unset BATCH; THREADS=20 UBATCH=2048 llm_config_load; llm_config_save
  [[ $BATCH == 2048 ]] || fail "BATCH folgt UBATCH"
  [[ -f "$MAG_LLM_ENV" ]] || fail "llm.env nicht geschrieben"
  unset THREADS UBATCH LOAD_ARGS THREADS_BATCH BATCH
  llm_config_load
  [[ $THREADS == 20 && $UBATCH == 2048 ]] || fail "Roundtrip llm.env"
  render_unit "$T/u4" 2>/dev/null
  grep -qF -- '--threads 20 \' "$T/u4" && grep -qF -- '--ubatch-size 2048 \' "$T/u4" || fail "render_unit"
  if UBATCH=4096 BATCH=2048 llm_config_load 2>/dev/null; then fail "UBATCH > BATCH angenommen"; fi
) || fail "llm-config"
ok "llm-config: Standard, Roundtrip, render_unit, Plausibilität, BATCH = UBATCH"

# 3) llm.env ist kein Shell-Code: fremde Zeilen werden ignoriert
printf 'THREADS=24\nrm -rf /\n$(touch %s/pwned)\nX=1\n' "$T" > "$T/evil.env"
(
  export MAG_LLM_ENV=$T/evil.env HELP_FILE=$T/help
  source ops/deepseek/llm-config.sh; llm_config_load
  [[ $THREADS == 24 ]] || fail "evil: THREADS"
) || fail "evil.env"
[[ ! -e "$T/pwned" ]] && ok "llm.env wird nicht ausgeführt"
# 4) Gateway: Client-Abbruch stoppt die Anfrage an llama
python3 -I -W ignore ops/audit/tests/test_gateway_abort.py >"$T/gw.log" 2>&1 || { cat "$T/gw.log"; fail "Gateway-Abbruch"; }
ok "Gateway: Abbruch beim Streamen und beim Prompt-Lesen, normaler Durchlauf"
grep -q 'MAG_ROOM_REASONING_EFFORT=' <<<"$(grep -v '^#' ops/mag-120.d/20-room-audit.conf)" && fail "reasoning_effort im Raum-Drop-in gesetzt"
ok "Raum-Drop-in ohne reasoning_effort"
echo "== 3.6-Tests grün"
