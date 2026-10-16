#!/usr/bin/env bash
# Run as root after building and checking the native source.
set -euo pipefail
SOURCE="$(cd -- "$(dirname -- "$0")/.." && pwd)"
RELEASE_ID="${MAG_RELEASE_ID:-$(date -u +%Y%m%dT%H%M%SZ)}"
RELEASE="/opt/mag-stack/releases/$RELEASE_ID"
BACKUP="/var/backups/mag-stack/$RELEASE_ID"
WHISPER_CLI="${MAG_WHISPER_SOURCE:-/home/chris/.cache/mag-stack/whisper-build/bin/whisper-cli}"
WHISPER_MODEL="${MAG_WHISPER_SOURCE_MODEL:-/home/chris/models/whisper/ggml-large-v3-turbo.bin}"
[[ $EUID == 0 ]] || { echo 'Run with sudo' >&2; exit 1; }
[[ "$RELEASE_ID" =~ ^[a-zA-Z0-9._-]+$ ]] || exit 1
for binary in magroom mag-browser magbot-native; do test -x "$SOURCE/build/$binary"; done
# Go is used only for WebRTC/UDP; the room, browsers and VM lifecycle remain C++.
if [[ ! -x "$SOURCE/build/mag-browser-webrtc" ]]; then
  command -v go >/dev/null || { echo 'Go >=1.23 required to build browser WebRTC gateway' >&2; exit 1; }
  (cd "$SOURCE/mag-room/webrtc" && go build -mod=mod -o "$SOURCE/build/mag-browser-webrtc" .)
fi
test -x "$SOURCE/build/mag-browser-webrtc"
test -x "$WHISPER_CLI"; test -r "$WHISPER_MODEL"
test ! -e "$RELEASE"
# Das Modell ist unabhängig vom Raum-Release (lädt evtl. noch herunter / wird geprüft).
# Nur informieren; der Raum zeigt den Modellstatus selbst an.
curl -fsS --max-time 5 http://127.0.0.1:8080/v1/models >/dev/null \
  || echo 'Hinweis: Modell-Backend noch nicht bereit – Deploy läuft weiter.' >&2
install -d -m 0755 /opt/mag-stack/releases "$RELEASE/bin" "$RELEASE/web" "$RELEASE/ops"
install -d -m 0700 "$BACKUP"
cp -a /etc/systemd/system/mag-120.service "$BACKUP/mag-120.service"
if [[ -f /etc/systemd/system/mag-browser-webrtc.service ]]; then
  cp -a /etc/systemd/system/mag-browser-webrtc.service "$BACKUP/mag-browser-webrtc.service"
fi
cp -a /usr/local/bin/magbot "$BACKUP/magbot"
cp -a /var/lib/magbot/room "$BACKUP/room"
if [[ -L /opt/mag-stack/current ]]; then readlink /opt/mag-stack/current > "$BACKUP/previous-release"; fi
install -m 0755 "$SOURCE/build/magroom" "$SOURCE/build/mag-browser" "$SOURCE/build/magbot-native" "$SOURCE/build/mag-browser-webrtc" "$WHISPER_CLI" "$RELEASE/bin/"
cp -a "$SOURCE/mag-room/web/." "$RELEASE/web/"
install -d "$RELEASE/web/static"
cp -a "$SOURCE/mag-room/static/." "$RELEASE/web/static/"
# Vendor once into a persistent local cache. Existing assets work offline;
# no CDN dependencies at runtime and no half-installed web release.
bash "$SOURCE/ops/vendor-terminal-assets.sh" "$SOURCE/build/terminal-assets"
cp -a "$SOURCE/build/terminal-assets/." "$RELEASE/web/static/"
cp -a "$SOURCE/ops/." "$RELEASE/ops/"
install -d -o magbot -g magbot -m 0750 /var/lib/mag-stack/models
install -o magbot -g magbot -m 0640 "$WHISPER_MODEL" /var/lib/mag-stack/models/ggml-large-v3-turbo.bin
# Preserve the previous bot-published Python-era UI, which has incompatible APIs.
if [[ ! -L /opt/mag-stack/current && -f /var/lib/magbot/room/frontend.html ]]; then
 mv /var/lib/magbot/room/frontend.html "$BACKUP/frontend.html"
fi
(cd "$RELEASE"; find bin web ops -type f -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS)
git -C "$SOURCE" rev-parse HEAD > "$RELEASE/SOURCE_COMMIT"
chown -R root:root "$RELEASE"
chmod -R a+rX "$RELEASE"
rollback() {
 echo 'Deployment failed; restoring previous room service' >&2
 cp -a "$BACKUP/mag-120.service" /etc/systemd/system/mag-120.service
 if [[ -f "$BACKUP/mag-browser-webrtc.service" ]]; then
   cp -a "$BACKUP/mag-browser-webrtc.service" /etc/systemd/system/mag-browser-webrtc.service
 else
   systemctl disable --now mag-browser-webrtc.service >/dev/null 2>&1 || true
   rm -f /etc/systemd/system/mag-browser-webrtc.service
 fi
 cp -a "$BACKUP/magbot" /usr/local/bin/magbot
 if [[ -f "$BACKUP/previous-release" ]]; then ln -sfn "$(cat "$BACKUP/previous-release")" /opt/mag-stack/current; fi
 if [[ -f "$BACKUP/frontend.html" ]]; then cp -a "$BACKUP/frontend.html" /var/lib/magbot/room/frontend.html; fi
 systemctl daemon-reload
 systemctl restart mag-120.service
 if [[ -f "$BACKUP/mag-browser-webrtc.service" ]]; then
   systemctl restart mag-browser-webrtc.service
 fi
}
# Drain: never cut a running reply or a queued message. Wait until the room is
# idle (no turn running, queue empty). MAG_DEPLOY_DRAIN_S (default 900 s);
# on timeout the deploy is deferred (exit 75) unless MAG_DEPLOY_FORCE=1 –
# the queue is persisted and resumed after the restart either way.
drain_s="${MAG_DEPLOY_DRAIN_S:-900}"; waited=0
while :; do
  st=$(curl -fsS --max-time 5 http://127.0.0.1:8766/api/state 2>/dev/null \
       | python3 -c 'import json,sys;d=json.load(sys.stdin);print("%d %d"%(bool(d.get("busy")),int(d.get("queue",0))))' 2>/dev/null || echo "0 0")
  read -r busy queued <<<"$st"
  [[ "$busy" == 0 && "$queued" == 0 ]] && break
  if (( waited == 0 )); then echo "Deploy wartet: Antwort läuft ($busy) / $queued in der Warteschlange – warte bis max. ${drain_s}s …" >&2; fi
  if (( waited >= drain_s )); then
    if [[ "${MAG_DEPLOY_FORCE:-0}" == 1 ]]; then echo "Drain-Timeout – MAG_DEPLOY_FORCE=1: deploye trotzdem (Warteschlange wird nach dem Neustart fortgesetzt)." >&2; break; fi
    echo "Drain-Timeout nach ${drain_s}s: Raum ist nicht leer – Deploy verschoben (Release $RELEASE_ID bleibt unbenutzt). Später erneut ausführen oder MAG_DEPLOY_FORCE=1." >&2
    rm -rf "$RELEASE"; exit 75
  fi
  sleep 5; waited=$((waited + 5))
done
trap rollback ERR
# Retire the old installer timer before changing its service executable.
if systemctl cat mag-120-finalize.timer >/dev/null 2>&1; then systemctl disable --now mag-120-finalize.timer; fi
ln -s "$RELEASE" /opt/mag-stack/.current-next
mv -Tf /opt/mag-stack/.current-next /opt/mag-stack/current
# Compatibility name is deliberately retained for existing guest wrappers/sudoers.
install -m 0755 "$RELEASE/bin/magbot-native" /usr/local/bin/magbot
install -m 0755 "$SOURCE/ops/spawnbot" "$SOURCE/ops/despawnbot" /usr/local/bin/
install -m 0755 "$SOURCE/ops/magbot-guest-run" "$SOURCE/ops/magbot-agent-ctl" /usr/local/sbin/
install -m 0440 "$SOURCE/ops/magbot-guest.sudoers" /etc/sudoers.d/magbot-guest
visudo -cf /etc/sudoers.d/magbot-guest >/dev/null
install -m 0644 "$SOURCE/ops/mag-120.service" "$SOURCE/ops/mag-browser-webrtc.service" /etc/systemd/system/
install -m 0644 "$SOURCE/ops/mag-stack-check.service" "$SOURCE/ops/mag-stack-check.timer" "$SOURCE/ops/mag-git-gateway.socket" "$SOURCE/ops/mag-git-gateway.service" "$SOURCE/ops/mag-agents-network.service" /etc/systemd/system/
# Read-only backing image; existing VM overlays remain untouched.
chmod 0440 /var/lib/libvirt/images/magbot/base/golden.qcow2
systemctl daemon-reload
systemctl enable --now mag-agents-network.service
systemctl restart mag-agents-network.service
systemctl enable mag-120.service
systemctl restart mag-120.service
systemctl enable --now mag-browser-webrtc.service
systemctl restart mag-browser-webrtc.service
# /readyz = Raum bereit (Modellstatus steht im JSON, ist aber kein Rollback-Grund)
for i in {1..30}; do if curl -fsS --max-time 2 http://127.0.0.1:8766/readyz >/dev/null; then break; fi; sleep 1; done
curl -fsS --max-time 5 http://127.0.0.1:8766/readyz
curl -fsS --max-time 5 http://127.0.0.1:18784/health
/usr/local/bin/magbot status
for unit in mag-chat.service pm2-chris.service; do if systemctl cat "$unit" >/dev/null 2>&1; then systemctl disable --now "$unit"; fi; done
systemctl enable --now mag-stack-check.timer mag-git-gateway.socket
trap - ERR
printf '\nInstalled release %s; rollback snapshot %s\n' "$RELEASE_ID" "$BACKUP"
