#!/usr/bin/env bash
# Read-only Health-Check (mag-stack-check.timer, minütlich). Probes bleiben kurz (--max-time 5).
# Modell: Download/Prüfung/Laden sind ein normaler Zustand (Warnung, kein Fehler);
# nur eine fehlgeschlagene Prüfung oder ein nicht erreichbares Backend bei freigegebenem Modell ist ein Fehler.
set -euo pipefail
systemctl is-active --quiet mag-120.service mag-browser-webrtc.service gitea.service tailscaled.service
# Zentrales Logging, sobald installiert
for unit in mag-audit-nft.service mag-audit-gateway.service mag-audit-webproxy.service; do
  if systemctl cat "$unit" >/dev/null 2>&1; then systemctl is-active --quiet "$unit"; fi
done
ready=$(curl --fail --silent --show-error --max-time 5 http://127.0.0.1:8766/readyz)
printf '%s\n' "$ready"
state=$(python3 -I -c 'import json,sys; print(json.loads(sys.argv[1]).get("model",{}).get("state",""))' "$ready")
case "$state" in
  ready)
    : ;; # model ready via /readyz (DeepSeek mag-llm retired; Ornith health is the status)
  downloading|verifying|verified|starting|loading)
    echo "WARN: Modell noch nicht bereit ($state)" >&2 ;;
  *)
    echo "FEHLER: Modellstatus '$state'" >&2; exit 1 ;;
esac
# The room may be healthy while one of the two Ornith nodes has failed.
# Check both real inference instances, not just the room's HTTP listener.
if [[ -r /etc/mag-stack/secondary-model.env ]] &&
   grep -Fxq 'MAG_SECONDARY_MODEL=ornith' /etc/mag-stack/secondary-model.env; then
  for p in 8191 8192; do
    curl --noproxy '*' --fail --silent --show-error --max-time 5       "http://127.0.0.1:$p/health" >/dev/null ||
      { echo "FEHLER: Ornith :$p nicht bereit" >&2; exit 1; }
  done
fi
curl --fail --silent --show-error --max-time 5 http://127.0.0.1:18784/health
printf '\n'
/usr/local/bin/magbot status
