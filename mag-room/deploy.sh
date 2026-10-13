#!/usr/bin/env bash
set -euo pipefail

DIR="/home/chris/mag-room"
STAMP="$(date -u +%Y%m%d-%H%M%S)"

echo "[1/8] Backend /healthz"
curl -fsS http://127.0.0.1:8080/v1/models >/dev/null && echo "  llama.cpp :8080 OK"

echo "[2/8] VM-Wrapper + sudoers"
install -o root -g root -m 0755 "$DIR/magbot-guest-run" /usr/local/sbin/magbot-guest-run
install -o root -g root -m 0440 "$DIR/magbot-guest.sudoers" /etc/sudoers.d/magbot-guest
visudo -cf /etc/sudoers.d/magbot-guest >/dev/null && echo "  sudoers OK"

echo "[3/8] Brave-Env (root 0600)"
install -o root -g root -m 0600 "$DIR/mag-room.env" /etc/default/mag-room

echo "[4/8] Backup App"
[ -f /opt/magbot/app.py ] && cp -a /opt/magbot/app.py "/opt/magbot/app.py.bak-room-$STAMP" \
  && echo "  -> /opt/magbot/app.py.bak-room-$STAMP"

echo "[5/8] App + Dienst installieren"
install -o root -g root -m 0755 "$DIR/app.py" /opt/magbot/app.py
install -o root -g root -m 0644 "$DIR/mag-120.service" /etc/systemd/system/mag-120.service

echo "[6/8] Dienst neu starten"
systemctl daemon-reload
systemctl restart mag-120
sleep 2
systemctl is-active mag-120

echo "[7/8] Endpunkte"
echo -n "  healthz: "; curl -fsS -m5 http://127.0.0.1:8766/healthz; echo
echo -n "  bots:    "; curl -fsS -m5 http://127.0.0.1:8766/api/bots; echo

echo "[8/8] Wrapper-Test (Root -> VM agent1)"
/usr/local/sbin/magbot-guest-run agent1 'echo wrapper-ok; uname -r' | sed 's/^/  /'

echo "Fertig. Tailscale: https://mag.tail3e02ae.ts.net:8446"
