#!/usr/bin/env bash
# Wo verbringt llama seine Zeit? CPU-Profil des LAUFENDEN mag-llm (perf, nur lesend, kein zweiter
# Modellprozess). Die Last kommt per HTTP über das Audit-Gateway:
#   1) Generierung: eine Anfrage mit 400 Tokens, 30 s Profil
#   2) Prompt-Lesen: eine Anfrage mit ~1.700 Prompt-Tokens, 30 s Profil
# Ausgabe: /var/tmp/mag-profile-<zeit>.txt (Funktionen nach Anteil) – bitte zurückschicken.
#
#   sudo bash ops/deepseek/profile.sh
# Benötigt perf (linux-tools-$(uname -r)); fehlt es, wird es per apt installiert, sonst übersprungen.
set -uo pipefail
URL=${URL:-http://127.0.0.1:8080/v1/chat/completions}
SECS=${SECS:-30}
OUT=/var/tmp/mag-profile-$(date +%Y%m%d-%H%M).txt
TMP=$(mktemp -d /var/tmp/mag-profile.XXXXXX); trap 'rm -rf "$TMP"' EXIT
[[ $EUID -eq 0 ]] || { echo "bitte mit sudo"; exit 1; }

perf_ok() { command -v perf >/dev/null && perf --version >/dev/null 2>&1; }
if ! perf_ok; then
  apt-get install -y --no-install-recommends linux-tools-common "linux-tools-$(uname -r)" >/dev/null 2>&1 || true
fi
perf_ok || { echo "perf nicht verfügbar (linux-tools-$(uname -r) nicht installierbar) – Profil übersprungen"; exit 0; }
pid=$(systemctl show -p MainPID --value mag-llm.service)
[[ -n "$pid" && "$pid" != 0 ]] || { echo "mag-llm läuft nicht"; exit 1; }
busy=$(curl -fsS --max-time 3 http://127.0.0.1:8766/api/state 2>/dev/null | python3 -I -c 'import json,sys;print(json.load(sys.stdin).get("busy"))' 2>/dev/null || echo "?")
[[ "$busy" == True ]] && { echo "Raum ist beschäftigt – später erneut"; exit 1; }

load() {   # $1 = Wörter im Prompt, $2 = max_tokens; läuft im Hintergrund
  python3 -I - "$URL" "$1" "$2" <<'PY' >/dev/null 2>&1 &
import json, sys, time, urllib.request
url, n, mt = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
W = "Konstanz Bodensee Wohnung Prozess Planung Server Speicher Modell Messung Ergebnis".split()
text = f"Profil {time.time_ns()}: " + " ".join(W[i % len(W)] for i in range(n)) + "\nErzähle ausführlich."
req = {"model": "deepseek-v4-flash-0731", "messages": [{"role": "user", "content": text}], "max_tokens": mt,
       "temperature": 0, "stream": False, "cache_prompt": False, "ignore_eos": True,
       "chat_template_kwargs": {"enable_thinking": False}}
urllib.request.urlopen(urllib.request.Request(url, json.dumps(req).encode(), {"Content-Type": "application/json"}), timeout=3600).read()
PY
  echo $!
}

profile() {   # $1 = Name, $2 = Wartezeit bis zur Messung
  local name=$1 lead=$2
  sleep "$lead"
  perf record -q -F 499 -g -p "$pid" -o "$TMP/$name.data" -- sleep "$SECS" >/dev/null 2>&1
  {
    echo "===== $name: Funktionen (Selbstanteil) ====="
    perf report -i "$TMP/$name.data" --stdio --no-children --sort dso,sym --percent-limit 0.5 2>/dev/null \
      | grep -vE '^#|^$' | grep -E '%' | head -45
    echo
    echo "===== $name: nach Thread-Name ====="
    perf report -i "$TMP/$name.data" --stdio --no-children --sort comm --percent-limit 1 2>/dev/null | grep -E '%' | head -10
    echo
  } >> "$OUT"
}

{ echo "# mag-llm Profil $(date -Is)  PID $pid"; tr '\0' ' ' < "/proc/$pid/cmdline" | grep -oE -- '--(load-mode [^ ]+|threads(-batch)? [0-9]+|(u)?batch-size [0-9]+)' | tr '\n' ' '; echo; echo; } > "$OUT"
echo "== Generierung (Profil ${SECS}s)"
lp=$(load 30 400); profile generierung 8; wait "$lp" 2>/dev/null
echo "== Prompt-Lesen (Profil ${SECS}s)"
lp=$(load 1300 4); profile prompt 3; wait "$lp" 2>/dev/null
echo "Profil: $OUT"; head -60 "$OUT"
