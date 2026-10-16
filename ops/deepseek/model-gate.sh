#!/usr/bin/env bash
# Modell-Freigabe für mag-llm (DeepSeek-V4-Flash-0731).
#
#   model-gate.sh run           (root, mag-model-gate.timer) Download beobachten -> SHA-256 prüfen ->
#                               Freigabe-Stempel schreiben -> mag-llm starten
#   model-gate.sh check-stamp   (ExecStartPre von mag-llm) Stempel passt zu den aktuellen Dateien?
#   model-gate.sh status        Status anzeigen
#
# Status für das Frontend: /var/lib/mag-llm/status.json  (state: downloading|verifying|verified|failed)
# magroom kombiniert das mit /health des Backends (loading/ready) und zeigt es im Raum an.
# mag-llm startet NIE vor einer bestandenen Vollprüfung (ConditionPathExists auf den Stempel).
set -euo pipefail
HERE=$(dirname "$(readlink -f "$0")")
# shellcheck source=model-manifest.sh
source "${MAG_MODEL_MANIFEST:-$HERE/model-manifest.sh}"
STATE_DIR=${MAG_LLM_STATE:-/var/lib/mag-llm}
STATUS=$STATE_DIR/status.json
STAMP=$STATE_DIR/verified.stamp
FAILED=$STATE_DIR/failed.stamp

meta_sig() {   # Größe, mtime, Inode aller Teile (ändert sich bei jedem neuen Download)
  local p f
  for p in "${MODEL_PARTS[@]}"; do
    f=$(model_part "$p")
    if [[ -f "$f" ]]; then stat -Lc "$p:%s:%Y:%i" "$f"; else echo "$p:missing"; fi
  done
}
write_status() {   # state detail
  [[ -w "$STATE_DIR" ]] || return 0
  python3 -I - "$STATUS" "$1" "$2" <<'PY'
import json, os, sys, time
path, state, detail = sys.argv[1:4]
tmp = path + ".tmp"
with open(tmp, "w") as fh:
    json.dump({"state": state, "detail": detail,
               "updated": time.strftime("%Y-%m-%dT%H:%M:%S%z")}, fh, ensure_ascii=False)
os.chmod(tmp, 0o644)
os.replace(tmp, path)
PY
}
gb() { awk -v b="$1" 'BEGIN{printf "%.0f", b/1e9}'; }

download_state() {   # gibt 0 zurück, wenn alle Teile vollständig sind
  local p f sz have=0 done=0 active=""
  for p in "${MODEL_PARTS[@]}"; do
    f=$(model_part "$p")
    sz=0; [[ -f "$f" ]] && sz=$(stat -Lc %s "$f")
    for side in "$f.part" "$f.aria2" "$f.incomplete" "$f.lock"; do
      if [[ -e "$side" ]]; then active=1; fi
    done
    (( sz > MODEL_SIZE[$p] )) && sz=${MODEL_SIZE[$p]}
    have=$((have + sz))
    [[ $sz -eq ${MODEL_SIZE[$p]} ]] && done=$((done + 1))
  done
  local total; total=$(model_total_bytes)
  DL_DETAIL="$done/${#MODEL_PARTS[@]} Teile vollständig, $(gb $have) von $(gb "$total") GB ($((have * 100 / total)) %)"
  [[ $done -eq ${#MODEL_PARTS[@]} && -z "$active" ]]
}

cmd_check_stamp() {
  [[ -f "$STAMP" ]] || { echo "kein Freigabe-Stempel ($STAMP) – Modell noch nicht geprüft" >&2; exit 1; }
  if [[ "$(head -n 5 "$STAMP")" != "$(meta_sig)" ]]; then
    echo "Modelldateien haben sich seit der Prüfung geändert – neue Prüfung nötig (mag-model-gate)" >&2
    exit 1
  fi
}

cmd_run() {
  install -d -m 0755 "$STATE_DIR"
  local sig; sig=$(meta_sig)
  # 1) schon geprüft und unverändert -> nur sicherstellen, dass mag-llm läuft
  if [[ -f "$STAMP" && "$(head -n 5 "$STAMP")" == "$sig" ]]; then
    write_status verified "SHA-256 aller 5 Teile geprüft"
    if ! systemctl is-active --quiet mag-llm.service && ! systemctl is-failed --quiet mag-llm.service; then
      systemctl start --no-block mag-llm.service
    fi
    return 0
  fi
  rm -f "$STAMP"
  # 2) Download noch nicht fertig
  if ! download_state; then
    write_status downloading "$DL_DETAIL"
    echo "Download läuft: $DL_DETAIL"
    return 0
  fi
  # 3) frühere Fehlprüfung derselben Dateien nicht endlos wiederholen
  if [[ -f "$FAILED" && "$(head -n 5 "$FAILED")" == "$sig" ]]; then
    write_status failed "$(tail -n 1 "$FAILED")"
    return 1
  fi
  # 4) Vollprüfung
  local p f got i=0 n=${#MODEL_PARTS[@]} shas=""
  for p in "${MODEL_PARTS[@]}"; do
    i=$((i + 1)); f=$(model_part "$p")
    write_status verifying "Teil $i/$n wird geprüft ($(gb "${MODEL_SIZE[$p]}") GB)"
    got=$(nice -n 10 ionice -c3 sha256sum "$f" | cut -d' ' -f1)
    if [[ "$got" != "${MODEL_SHA[$p]}" ]]; then
      local msg="Teil $p: SHA-256 stimmt nicht (Datei beschädigt oder falsche Version) – neu herunterladen"
      { echo "$sig"; echo "$msg"; } > "$FAILED"
      write_status failed "$msg"
      echo "$msg" >&2
      return 1
    fi
    shas+="$p $got"$'\n'
  done
  # 5) Freigabe: Stempel (erste 5 Zeilen = Datei-Signatur) + Prüfsummen
  { echo "$sig"; printf '%s' "$shas"; date -Is; } > "$STAMP.tmp"
  chmod 0644 "$STAMP.tmp"; mv -f "$STAMP.tmp" "$STAMP"
  rm -f "$FAILED"
  write_status verified "SHA-256 aller 5 Teile geprüft"
  echo "Modell geprüft – starte mag-llm (nur wenn nicht schon aktiv; nie restart/unload)"
  if systemctl is-active --quiet mag-llm.service; then
    echo "mag-llm läuft bereits – kein Eingriff"
  else
    systemctl reset-failed mag-llm.service 2>/dev/null || true
    systemctl start --no-block mag-llm.service
  fi
}

case "${1:-run}" in
  run) cmd_run ;;
  check-stamp) cmd_check_stamp ;;
  status) cat "$STATUS" 2>/dev/null || echo '{"state":"unknown"}'; echo; download_state || true; echo "$DL_DETAIL" ;;
  *) echo "Usage: model-gate.sh run|check-stamp|status" >&2; exit 2 ;;
esac
