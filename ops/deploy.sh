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
exec 8>/run/lock/mag-private-deploy.lock
flock -n 8 || { echo 'Another MAG deploy/ingress install is running' >&2; exit 1; }
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
# No release state mutation before complete nginx/Tailnet/Owner-probe validation.
NGINX_SYNC="$SOURCE/ops/private-access/deploy-nginx.sh"
bash "$NGINX_SYNC" preflight
# Das Modell ist unabhängig vom Raum-Release (lädt evtl. noch herunter / wird geprüft).
# Nur informieren; der Raum zeigt den Modellstatus selbst an.
curl -fsS --max-time 5 http://127.0.0.1:8080/v1/models >/dev/null \
  || echo 'Hinweis: Modell-Backend noch nicht bereit – Deploy läuft weiter.' >&2
install -d -m 0755 /opt/mag-stack/releases "$RELEASE/bin" "$RELEASE/web" "$RELEASE/ops"
install -d -m 0700 "$BACKUP"
install -m 0700 "$NGINX_SYNC" "$BACKUP/deploy-nginx.sh"
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
# Stage pinned German Moonshine model/WASM while old room remains active.
# If staging fails, set -e aborts before the current-release symlink changes.
bash "$SOURCE/ops/vendor-speech-assets.sh" "$SOURCE/build/speech-assets"
cp -a "$SOURCE/build/speech-assets/." "$RELEASE/web/static/"
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
# All changes after this point must unwind both the native room and nginx.
# Never restart or stop mag-llm (including on failed deploy).
rollback() {
 echo 'Deployment failed; restoring previous room service and nginx' >&2
 cp -a "$BACKUP/mag-120.service" /etc/systemd/system/mag-120.service
 if [[ -f "$BACKUP/mag-browser-webrtc.service" ]]; then
   cp -a "$BACKUP/mag-browser-webrtc.service" /etc/systemd/system/mag-browser-webrtc.service
 else
   systemctl disable --now mag-browser-webrtc.service >/dev/null 2>&1 || true
   rm -f /etc/systemd/system/mag-browser-webrtc.service
 fi
 cp -a "$BACKUP/magbot" /usr/local/bin/magbot
 if [[ -f "$BACKUP/previous-release" ]]; then
   ln -sfn "$(cat "$BACKUP/previous-release")" /opt/mag-stack/.previous-rollback
   mv -Tf /opt/mag-stack/.previous-rollback /opt/mag-stack/current
 fi
 if [[ -f "$BACKUP/frontend.html" ]]; then
   cp -a "$BACKUP/frontend.html" /var/lib/magbot/room/frontend.html
 fi
 systemctl daemon-reload
 systemctl restart mag-120.service
 if [[ -f "$BACKUP/mag-browser-webrtc.service" ]]; then
   systemctl restart mag-browser-webrtc.service
 fi
 bash "$NGINX_SYNC" rollback
}
deployment_started=0
deploy_exit() {
 local rc=$?
 trap - EXIT ERR INT TERM
 if (( rc != 0 && deployment_started )); then
   rollback || echo 'Rollback incomplete: retain SSH rescue channel and inspect /var/backups/mag-stack' >&2
 fi
 exit "$rc"
}

# Drain: never cut a running reply or a queued message. Wait until the room is
# idle (no turn running, queue empty). MAG_DEPLOY_DRAIN_S (default 900 s);
# on timeout the deploy is deferred (exit 75) unless MAG_DEPLOY_FORCE=1 –
# the queue is persisted and resumed after the restart either way.
drain_s="${MAG_DEPLOY_DRAIN_S:-900}"; waited=0
while :; do
  st=$(curl -fsS --max-time 5 http://127.0.0.1:8766/api/state 2>/dev/null \
       | python3 -c 'import json,sys;d=json.load(sys.stdin);print("%d %d"%(bool(d.get("busy")),0 if d.get("queue_paused") else int(d.get("queue",0))))' 2>/dev/null || echo "0 0")
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
# Owner smoke must come from a second authenticated Tailnet device.
# nginx.stage snapshots the old vhost and validates the candidate BEFORE
# switching symlinks or restarting the room.
MAG_NGINX_TXN="$BACKUP"; export MAG_NGINX_TXN
deployment_started=1
trap deploy_exit EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
bash "$NGINX_SYNC" stage
# Retire the old installer timer before changing its service executable.
if systemctl cat mag-120-finalize.timer >/dev/null 2>&1; then systemctl disable --now mag-120-finalize.timer; fi
ln -s "$RELEASE" /opt/mag-stack/.current-next
mv -Tf /opt/mag-stack/.current-next /opt/mag-stack/current
# Compatibility name is deliberately retained for existing guest wrappers/sudoers.
install -m 0755 "$RELEASE/bin/magbot-native" /usr/local/bin/magbot
install -m 0755 "$SOURCE/ops/spawnbot" "$SOURCE/ops/despawnbot" /usr/local/bin/
install -m 0755 "$SOURCE/ops/magbot-guest-run" "$SOURCE/ops/magbot-agent-ctl" "$SOURCE/ops/magbot-netmode" /usr/local/sbin/
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
# Test effective nginx configuration and reload, then assert actual Owner
# identity + both public frontend routes across the Tailnet from Owner device.
bash "$NGINX_SYNC" activate
bash "$NGINX_SYNC" smoke
/usr/local/bin/magbot status
for unit in mag-chat.service pm2-chris.service; do if systemctl cat "$unit" >/dev/null 2>&1; then systemctl disable --now "$unit"; fi; done
systemctl enable --now mag-stack-check.timer mag-git-gateway.socket
rm -f "$MAG_NGINX_TXN/nginx.staged"
deployment_started=0
trap - EXIT INT TERM
printf '\nInstalled release %s; rollback snapshot %s\n' "$RELEASE_ID" "$BACKUP"
