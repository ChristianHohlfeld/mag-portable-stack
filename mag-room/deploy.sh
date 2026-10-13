#!/usr/bin/env bash
set -euo pipefail

DIR="/home/chris/mag-room"
STAMP="$(date -u +%Y%m%d-%H%M%S)"
TOOLS_VENV="/opt/magbot/tools-venv"
STATE_DIR="/var/lib/magbot/room"

echo "[1/9] Backend /healthz"
curl -fsS http://127.0.0.1:8080/v1/models >/dev/null && echo "  llama.cpp :8080 OK"

echo "[2/9] VM-Wrapper, Agent-CLI + sudoers"
install -o root -g root -m 0755 "$DIR/magbot-guest-run" /usr/local/sbin/magbot-guest-run
install -o root -g root -m 0755 "$DIR/magbot-agent-ctl" /usr/local/sbin/magbot-agent-ctl
install -o root -g root -m 0440 "$DIR/magbot-guest.sudoers" /etc/sudoers.d/magbot-guest
visudo -cf /etc/sudoers.d/magbot-guest >/dev/null && echo "  sudoers OK"

echo "[3/9] Brave-Env (root 0600)"
install -o root -g root -m 0600 "$DIR/mag-room.env" /etc/default/mag-room

echo "[4/9] Zustands-Verzeichnis ($STATE_DIR)"
install -d -o magbot -g magbot -m 0750 "$STATE_DIR"
install -d -o magbot -g magbot -m 0750 "$STATE_DIR/files"

echo "[5/9] Host-Werkzeug-Venv ($TOOLS_VENV)"
if [ ! -x "$TOOLS_VENV/bin/python" ]; then
  python3 -m venv "$TOOLS_VENV"
fi
if ! "$TOOLS_VENV/bin/python" -c 'import gtts, rapidocr_onnxruntime' 2>/dev/null; then
  "$TOOLS_VENV/bin/pip" install --quiet --upgrade pip
  "$TOOLS_VENV/bin/pip" install --quiet gTTS rapidocr-onnxruntime
fi
install -o root -g root -m 0755 "$DIR/roomtools.py" /opt/magbot/roomtools.py
echo "  tts/ocr bereit"

echo "[6/9] Backup App"
[ -f /opt/magbot/app.py ] && cp -a /opt/magbot/app.py "/opt/magbot/app.py.bak-room-$STAMP" \
  && echo "  -> /opt/magbot/app.py.bak-room-$STAMP"

echo "[7/9] App + Dienst installieren"
install -o root -g root -m 0755 "$DIR/app.py" /opt/magbot/app.py
install -d -o root -g root -m 0755 /opt/magbot/static
install -o root -g root -m 0644 "$DIR"/static/* /opt/magbot/static/
install -o root -g root -m 0644 "$DIR/mag-120.service" /etc/systemd/system/mag-120.service

echo "[8/9] Dienst neu starten"
systemctl daemon-reload
systemctl restart mag-120
sleep 2
systemctl is-active mag-120

echo "[9/9] Endpunkte + Wrapper-Test"
echo -n "  healthz: "; curl -fsS -m5 http://127.0.0.1:8766/healthz; echo
echo -n "  bots:    "; curl -fsS -m5 http://127.0.0.1:8766/api/bots; echo
echo -n "  files:   "; curl -fsS -m5 http://127.0.0.1:8766/api/files; echo
/usr/local/sbin/magbot-guest-run agent1 'echo wrapper-ok; uname -r' | sed 's/^/  /'

echo "Fertig. Tailscale: https://mag.tail3e02ae.ts.net:8446"
