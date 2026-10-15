#!/usr/bin/env bash
# Rotiert Raum-Bridge-Token und die UDP-Schlüssel aller Agent-VMs (beide standen über sudo-Logs
# im Klartext im Journal). magroom erzeugt fehlende Werte beim Start neu; die Browser-Controller in
# den VMs melden dann 401 und werden mit den neuen Werten neu gestartet – Chromium, Tabs, Profile
# und VM-Disks bleiben unverändert.
#
#   sudo bash ops/security/rotate-room-secrets.sh            Vorschau
#   sudo bash ops/security/rotate-room-secrets.sh --apply    rotieren (Raum ~10 s nicht erreichbar)
#
# Vorher: neuen sudoers-Stand (Defaults:magbot !syslog) deployen, sonst landen die neuen Werte
# beim nächsten Browser-Start wieder im Journal.
set -euo pipefail
ROOM=${MAG_ROOM_STATE:-/var/lib/magbot/room}
[[ $EUID -eq 0 ]] || { echo "bitte mit sudo"; exit 1; }
grep -qs '!syslog' /etc/sudoers.d/magbot-guest || { echo "Abbruch: /etc/sudoers.d/magbot-guest ohne '!syslog' – erst Branch deployen"; exit 1; }
BK=/var/backups/mag-stack/secrets-rotated-$(date -u +%Y%m%dT%H%M%SZ)
echo "Raum-State: $ROOM"
ls -la "$ROOM/token" "$ROOM"/browser-udp-keys/ 2>/dev/null | sed 's/^/  /'
[[ "${1:-}" == "--apply" ]] || { echo "(Vorschau – mit --apply rotieren)"; exit 0; }

# Alte Werte für die Journal-Bereinigung in secrets.list übernehmen (Maskierung + Leak-Suche)
if [[ -f /etc/mag-audit/secrets.list ]]; then
  { echo "old_room_bridge_token=$(cat "$ROOM/token")"
    for k in "$ROOM"/browser-udp-keys/*.key; do [[ -f $k ]] && echo "old_udp_key_$(basename "$k" .key)=$(cat "$k")"; done
  } >> /etc/mag-audit/secrets.list
fi
install -d -m 0700 "$BK"
systemctl stop mag-browser-webrtc.service mag-120.service
mv "$ROOM/token" "$BK/token"
mkdir -p "$BK/browser-udp-keys"
for k in "$ROOM"/browser-udp-keys/*.key; do [[ -f $k ]] && mv "$k" "$BK/browser-udp-keys/"; done
systemctl start mag-120.service
for i in $(seq 1 30); do curl -fsS --max-time 2 http://127.0.0.1:8766/healthz >/dev/null && break; sleep 1; done
systemctl start mag-browser-webrtc.service
# Neue Werte ebenfalls maskieren (Gateway/Proxy ersetzen sie überall durch [SECRET:name])
if [[ -f /etc/mag-audit/secrets.list ]]; then
  echo "room_bridge_token=$(cat "$ROOM/token")" >> /etc/mag-audit/secrets.list
fi
echo "Rotiert. Alte Werte (nur root): $BK"
echo "Neue UDP-Schlüssel entstehen je VM beim nächsten Browser-Start; danach:"
echo "  for k in $ROOM/browser-udp-keys/*.key; do echo \"udp_key_\$(basename \$k .key)=\$(cat \$k)\"; done | sudo tee -a /etc/mag-audit/secrets.list"
echo "Dann Journal bereinigen: sudo bash ops/security/journal-purge.sh (--yes)"
