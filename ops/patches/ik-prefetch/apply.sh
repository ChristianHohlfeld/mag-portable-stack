#!/usr/bin/env bash
# mag ik-Prefetch (Runde 3.8 + 0003) – baut ik_llama.cpp mit Software-Prefetch und stellt mag-llm darauf um.
#
#   sudo bash apply.sh                          Prefetch-Distanz 4096 B (eingebauter Standard)
#   sudo Q8_PF=8192 MXFP4_PF=8192 bash apply.sh andere Distanz (Drop-in 61-mag-prefetch.conf), 0 = aus
#   sudo bash rollback.sh                       zurück auf den Stand vor apply.sh
#
# Was passiert:
#   1. Vorprüfung: mag-llm bereit (8090/health), Pin aktiv, Engine ik – sonst Abbruch ohne Änderung
#   2. Sicherung von Unit, llm.env und Prefetch-Drop-in
#   3. 0003-iqk-prefetch.patch nach ops/deepseek/ik-patches/, Build mit build-ik.sh (neben dem laufenden Modell,
#      neuer Zielordner – das laufende Binary bleibt unangetastet)
#   4. IK_BIN in llm.env umstellen, Unit neu rendern (alle anderen Werte unverändert: -rtr, MAG_NUMA_LOCAL,
#      -t/-tb, ub, f16-KV), Neustart aus dem RAM, warten bis bereit
#   5. ein kurzer Schnelltest (17+25 und 128 Tokens) – kein Benchmark
#   Nicht bereit oder Absturz -> automatisch rollback.sh.
# Bewusst NICHT: drop_caches, numactl --membind, -ctk q8_0, Port 8080.
set -euo pipefail
PKG=$(cd "$(dirname "$(readlink -f "$0")")" && pwd)
REPO=${REPO:-/home/chris/mag-stack}
UNIT=/etc/systemd/system/mag-llm.service
DROPIN_DIR=/etc/systemd/system/mag-llm.service.d
PF_DROPIN=$DROPIN_DIR/61-mag-prefetch.conf
BACKUP_ROOT=/home/chris/mag-infra/systemd
TS=$(date +%Y%m%d%H%M%S)
BACKUP=$BACKUP_ROOT/ik-prefetch-$TS
LOG=/var/tmp/mag-ik-prefetch-$TS.log
PATCH_NAME=0003-iqk-prefetch.patch
Q8_PF=${Q8_PF:-}; MXFP4_PF=${MXFP4_PF:-}

[[ $EUID -eq 0 ]] || { echo "bitte mit sudo"; exit 1; }
exec > >(tee -a "$LOG") 2>&1
say()    { printf '\n== %s  [%s]\n' "$1" "$(date +%H:%M:%S)"; }
health() { curl -s -o /dev/null -w '%{http_code}' --max-time 3 http://127.0.0.1:8090/health 2>/dev/null; true; }
unlock() { install -d /run/systemd/system/mag-llm.service.d
           printf '[Unit]\nRefuseManualStop=no\n' > /run/systemd/system/mag-llm.service.d/99-upgrade.conf; systemctl daemon-reload; }
relock() { rm -f /run/systemd/system/mag-llm.service.d/99-upgrade.conf; systemctl daemon-reload; }
wait_ready() {   # $1 = Sekunden
  local deadline=$((SECONDS + $1)) last=0 code r0 r
  r0=$(systemctl show -p NRestarts --value mag-llm.service 2>/dev/null || echo 0)
  while (( SECONDS < deadline )); do
    code=$(health); [[ "$code" == 200 ]] && return 0
    r=$(systemctl show -p NRestarts --value mag-llm.service 2>/dev/null || echo 0)
    (( r - r0 >= 2 )) && { echo "   mag-llm ist $((r - r0))x abgestürzt"; return 1; }
    if (( SECONDS - last >= 60 )); then last=$SECONDS; printf '   %s  /health=%s\n' "$(date +%H:%M:%S)" "$code"; fi
    sleep 5
  done
  echo "   Zeitlimit erreicht"; return 1
}
fail() { echo "FEHLER: $1"; echo "-> automatischer Rollback"; BACKUP="$BACKUP" bash "$PKG/rollback.sh"; exit 1; }

for v in Q8_PF MXFP4_PF; do
  [[ -z "${!v}" || "${!v}" =~ ^[0-9]{1,6}$ ]] || { echo "$v='${!v}' ungültig (Bytes, 0..999999)"; exit 1; }
done

# ------------------------------------------------------------------ 1. Vorprüfung (ändert nichts)
say "1. Vorprüfung"
[[ -f "$REPO/ops/deepseek/build-ik.sh" && -f "$REPO/ops/deepseek/llm-config.sh" ]] || { echo "$REPO ohne 3.8 (build-ik.sh/llm-config.sh fehlen)"; exit 1; }
[[ "$(health)" == 200 ]] || { echo "mag-llm nicht bereit (8090/health) – erst den laufenden Stand in Ordnung bringen"; exit 1; }
systemctl is-active --quiet mag-model-pin.service || { echo "mag-model-pin läuft nicht – Neustart würde von der Platte laden"; exit 1; }
# shellcheck disable=SC1091
source "$REPO/ops/deepseek/llm-config.sh"
llm_config_load
[[ "${ENGINE:-}" == ik && -x "${IK_BIN:-/nonexistent}" ]] || { echo "Engine ist nicht ik (ENGINE=${ENGINE:-?}, IK_BIN=${IK_BIN:-?}) – Patch gilt nur für 3.8"; exit 1; }
OLD_BIN=$IK_BIN
echo "   läuft: $OLD_BIN"
echo "   Konfiguration: threads $THREADS/$THREADS_BATCH, batch $BATCH/$UBATCH, rtr $IK_RTR${IK_ENV:+, $IK_ENV}"
render_unit --check || { echo "aktuelle Konfiguration rendert nicht – Abbruch ohne Änderung"; exit 1; }

# ------------------------------------------------------------------ 2. Sicherung
say "2. Sicherung nach $BACKUP"
install -d -o chris -g chris "$BACKUP"
cp -a "$UNIT" "$BACKUP/mag-llm.service"
[[ -f "$MAG_LLM_ENV" ]] && cp -a "$MAG_LLM_ENV" "$BACKUP/llm.env"
[[ -f "$PF_DROPIN" ]] && cp -a "$PF_DROPIN" "$BACKUP/61-mag-prefetch.conf"
[[ -f "$REPO/ops/deepseek/ik-patches/$PATCH_NAME" ]] && cp -a "$REPO/ops/deepseek/ik-patches/$PATCH_NAME" "$BACKUP/$PATCH_NAME.vorher"
echo "$OLD_BIN" > "$BACKUP/ik-bin.vorher"
ln -sfn "$BACKUP" "$BACKUP_ROOT/ik-prefetch-latest"

# ------------------------------------------------------------------ 3. Patch + Build (Modell läuft weiter)
say "3. Patch einspielen und ik bauen (nice/ionice, halbe Kerne)"
install -m 0644 -o chris -g chris "$PKG/$PATCH_NAME" "$REPO/ops/deepseek/ik-patches/$PATCH_NAME"
NEW_BIN=$(runuser -u chris -- bash "$REPO/ops/deepseek/build-ik.sh" | tail -1) || fail "Build fehlgeschlagen (Patch passt nicht oder Compilerfehler)"
[[ -x "$NEW_BIN" ]] || fail "kein Binary: '$NEW_BIN'"
[[ "$NEW_BIN" != "$OLD_BIN" ]] || fail "Build lieferte dasselbe Binary wie vorher – Patch nicht übernommen?"
grep -q GGML_MXFP4_PF "$NEW_BIN" && grep -q GGML_Q8_PF "$NEW_BIN" || fail "Prefetch-Code fehlt im neuen Binary"
echo "   neu: $NEW_BIN"

# ------------------------------------------------------------------ 4. Umstellen + Neustart aus dem RAM
say "4. IK_BIN umstellen, Unit neu rendern, Neustart"
IK_BIN=$NEW_BIN llm_config_load
llm_config_save
render_unit "$UNIT" || fail "Unit ließ sich nicht rendern"
if [[ -n "$Q8_PF$MXFP4_PF" ]]; then
  install -d "$DROPIN_DIR"
  { echo "# mag ik-Prefetch: Distanz in Bytes (0 = aus), geschrieben von apply.sh $TS"; echo "[Service]"
    [[ -n "$Q8_PF" ]]    && echo "Environment=GGML_Q8_PF=$Q8_PF"
    [[ -n "$MXFP4_PF" ]] && echo "Environment=GGML_MXFP4_PF=$MXFP4_PF"; } > "$PF_DROPIN"
  echo "   Prefetch: Q8_0 ${Q8_PF:-4096} B, MXFP4 ${MXFP4_PF:-4096} B"
else
  rm -f "$PF_DROPIN"
  echo "   Prefetch: Standard 4096 B für beide"
fi
since=$(date '+%Y-%m-%d %H:%M:%S')
unlock; systemctl restart mag-llm.service; relock
wait_ready 5400 || fail "mag-llm nach dem Neustart nicht bereit"
grep -q -- "$NEW_BIN" <(tr '\0' ' ' < "/proc/$(systemctl show -p MainPID --value mag-llm.service)/cmdline") \
  || fail "mag-llm läuft nicht mit dem neuen Binary"
journalctl -u mag-llm --since "$since" --no-pager -o cat 2>/dev/null | grep -E 'mag_numa:' | tail -2 | sed 's/^/   /' || true

# ------------------------------------------------------------------ 5. Schnelltest (kein Benchmark)
say "5. Schnelltest"
r=$(curl -s --max-time 300 http://127.0.0.1:8090/completion -H 'Content-Type: application/json' \
      -d '{"prompt":"17+25=","n_predict":8,"temperature":0}' || true)
grep -q '42' <<<"$r" || fail "Schnelltest 17+25 liefert nicht 42: $(head -c 200 <<<"$r")"
r=$(curl -s --max-time 600 http://127.0.0.1:8090/completion -H 'Content-Type: application/json' \
      -d '{"prompt":"Erkläre in einem Absatz, wie ein Kühlschrank funktioniert.","n_predict":128,"temperature":0}' || true)
python3 -c 'import json,sys; t=json.loads(sys.argv[1]).get("timings",{}); g=t.get; print("   tg %.2f t/s über %d Tokens, pp %.1f t/s" % (g("predicted_per_second",0), g("predicted_n",0), g("prompt_per_second",0)))' "$r" 2>/dev/null \
  || echo "   (keine timings in der Antwort)"
echo
echo "Fertig. Alt: $OLD_BIN  ->  Neu: $NEW_BIN"
echo "Zurück: sudo bash $PKG/rollback.sh   Log: $LOG"
