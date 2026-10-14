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
test -x "$WHISPER_CLI"; test -r "$WHISPER_MODEL"
test ! -e "$RELEASE"
curl -fsS --max-time 5 http://127.0.0.1:8080/v1/models >/dev/null
install -d -m 0755 /opt/mag-stack/releases "$RELEASE/bin" "$RELEASE/web" "$RELEASE/ops"
install -d -m 0700 "$BACKUP"
cp -a /etc/systemd/system/mag-120.service "$BACKUP/mag-120.service"
cp -a /usr/local/bin/magbot "$BACKUP/magbot"
cp -a /var/lib/magbot/room "$BACKUP/room"
if [[ -L /opt/mag-stack/current ]]; then readlink /opt/mag-stack/current > "$BACKUP/previous-release"; fi
install -m 0755 "$SOURCE/build/magroom" "$SOURCE/build/mag-browser" "$SOURCE/build/magbot-native" "$WHISPER_CLI" "$RELEASE/bin/"
cp -a "$SOURCE/mag-room/web/." "$RELEASE/web/"
install -d "$RELEASE/web/static"
cp -a "$SOURCE/mag-room/static/." "$RELEASE/web/static/"
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
 cp -a "$BACKUP/magbot" /usr/local/bin/magbot
 if [[ -f "$BACKUP/previous-release" ]]; then ln -sfn "$(cat "$BACKUP/previous-release")" /opt/mag-stack/current; fi
 if [[ -f "$BACKUP/frontend.html" ]]; then cp -a "$BACKUP/frontend.html" /var/lib/magbot/room/frontend.html; fi
 systemctl daemon-reload
 systemctl restart mag-120.service
}
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
install -m 0644 "$SOURCE/ops/mag-120.service" /etc/systemd/system/mag-120.service
install -m 0644 "$SOURCE/ops/mag-stack-check.service" "$SOURCE/ops/mag-stack-check.timer" "$SOURCE/ops/mag-git-gateway.socket" "$SOURCE/ops/mag-git-gateway.service" "$SOURCE/ops/mag-agents-network.service" /etc/systemd/system/
# Read-only backing image; existing VM overlays remain untouched.
chmod 0440 /var/lib/libvirt/images/magbot/base/golden.qcow2
systemctl daemon-reload
systemctl enable --now mag-agents-network.service
systemctl restart mag-agents-network.service
systemctl enable mag-120.service
systemctl restart mag-120.service
for i in {1..30}; do if curl -fsS --max-time 2 http://127.0.0.1:8766/readyz >/dev/null; then break; fi; sleep 1; done
curl -fsS --max-time 5 http://127.0.0.1:8766/readyz
/usr/local/bin/magbot status
for unit in mag-chat.service pm2-chris.service; do if systemctl cat "$unit" >/dev/null 2>&1; then systemctl disable --now "$unit"; fi; done
systemctl enable --now mag-stack-check.timer mag-git-gateway.socket
trap - ERR
printf '\nInstalled release %s; rollback snapshot %s\n' "$RELEASE_ID" "$BACKUP"
