#!/usr/bin/env bash
# MAG release deploy. Run as root from the checkout:
#   cd ~/mag-stack && taskset -c 28-55 sudo -n bash ops/deploy.sh
#
# One job: build, stage a release, switch to it, verify, roll back on failure.
#  - Never waits for a running answer (the room persists its state) and never
#    stops, restarts or waits for a model: mag-modeld owns the model lifecycle.
#    A missing, downloading, loading or paused bot is not a deploy failure.
#  - Fails, and restores the previous release, only when the build breaks, the
#    new room does not answer /readyz or nginx no longer serves the room.
#  - Room data, VM overlays, models and credentials live outside releases and
#    are never touched (a rollback copy of the room state is kept).
# MAG_DEPLOY_DRAIN_S / MAG_DEPLOY_FORCE of earlier versions are accepted and
# ignored: there is no drain any more.
set -euo pipefail
SOURCE="$(cd -- "$(dirname -- "$0")/.." && pwd)"
[[ $EUID == 0 ]] || { echo 'Run with sudo' >&2; exit 1; }
# Everything started here (build, checksums) stays off the model cores 0-27;
# services restarted through systemd do not inherit this affinity.
if [[ -z ${MAG_BUILD_PINNED:-} ]] && taskset -c "${MAG_BUILD_CPUS:-28-55}" true 2>/dev/null; then
  MAG_BUILD_PINNED=1 exec taskset -c "${MAG_BUILD_CPUS:-28-55}" nice -n 10 bash "$0" "$@"
fi
# One hard deadline for the whole deploy. timeout(1) signals the process
# group, so no child can outlive it; the TERM trap below still rolls back.
if [[ -z ${MAG_DEPLOY_DEADLINE_ARMED:-} ]]; then
  MAG_DEPLOY_DEADLINE_ARMED=1 exec timeout --kill-after=180s "${MAG_DEPLOY_DEADLINE_S:-1500}s" bash "$0" "$@"
fi
exec 8>/run/lock/mag-private-deploy.lock
flock -n 8 || { echo 'Another MAG deploy/ingress install is running' >&2; exit 1; }

RELEASE_ID="${MAG_RELEASE_ID:-$(date -u +%Y%m%dT%H%M%SZ)}"
[[ $RELEASE_ID =~ ^[a-zA-Z0-9._-]+$ ]] || exit 1
RELEASE="/opt/mag-stack/releases/$RELEASE_ID"
BACKUP="/var/backups/mag-stack/$RELEASE_ID"
STATE=/var/lib/magbot/room
ROOM=http://127.0.0.1:8766
NGINX_SYNC="$SOURCE/ops/private-access/deploy-nginx.sh"
UNITS=/etc/systemd/system
log() { printf 'deploy: %s\n' "$*"; }
warn() { printf 'deploy: WARN: %s\n' "$*" >&2; }
soft() { "$@" || warn "optional step failed: $*"; }
wait_http() { # url [seconds]
  local i
  for ((i = 0; i < ${2:-45}; i++)); do
    curl --noproxy '*' -fsS --max-time 3 -o /dev/null "$1" 2>/dev/null && return 0
    sleep 1
  done
  return 1
}
test ! -e "$RELEASE"
# phase 0: nothing live changed, 1: nginx candidate staged, 2: release is
# current, 3: release verified and committed (never rolled back).
phase=0
on_exit() {
  local rc=$?
  trap - EXIT INT TERM
  if (( rc != 0 && phase >= 1 && phase < 3 )); then rollback; fi
  if (( rc != 0 && phase < 3 )); then rm -rf "$RELEASE"; fi
  exit "$rc"
}
trap on_exit EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
# A blank host or a room that is down has no running whois to probe.
FIRST_INSTALL=0
[[ -L /opt/mag-stack/current ]] && systemctl is-active --quiet mag-120.service || FIRST_INSTALL=1
export MAG_DEPLOY_FIRST_INSTALL=$FIRST_INSTALL

# ----------------------------------------------------------------- 1. build
# shellcheck source=build-targets.sh
source "$SOURCE/ops/build-targets.sh"
owner=$(stat -c %U "$SOURCE")
as_owner() { if [[ $owner == root ]]; then "$@"; else sudo -u "$owner" -H "$@"; fi; }
[[ -f $SOURCE/build/build.ninja ]] ||
  as_owner cmake -S "$SOURCE" -B "$SOURCE/build" -G Ninja -DCMAKE_BUILD_TYPE=Release >/dev/null
as_owner ninja -C "$SOURCE/build" -j"${MAG_BUILD_JOBS:-12}" "${MAG_BUILD_TARGETS[@]}" ||
  { echo 'deploy: build failed; nothing installed' >&2; exit 1; }
for binary in "${MAG_BUILD_TARGETS[@]}"; do
  [[ -x $SOURCE/build/$binary ]] || { echo "deploy: missing $binary" >&2; exit 1; }
done
# Go is used only for the WebRTC/UDP gateway of the VM browsers.
RTC="$SOURCE/build/mag-browser-webrtc"
if [[ ! -x $RTC || -n $(find "$SOURCE/mag-room/webrtc" -name '*.go' -newer "$RTC" -print -quit) ]]; then
  GO=$(command -v go || echo /snap/bin/go)
  (cd "$SOURCE/mag-room/webrtc" && as_owner "$GO" build -mod=mod -o "$RTC" .) ||
    { [[ -x $RTC ]] && warn 'WebRTC gateway rebuild failed; keeping the previous binary' || exit 1; }
fi

# --------------------------------------------------- 2. preflight + snapshot
bash "$NGINX_SYNC" preflight
install -d -m 0755 /opt/mag-stack/releases
install -d -m 0700 "$BACKUP"
# Encrypted pre-deploy snapshot (restic, ~10 s). A backup problem is reported
# but does not block a release: the rollback copy below is always taken.
if systemctl cat mag-backup-deploy.service >/dev/null 2>&1; then
  install -d -m 0755 /usr/local/lib/mag-backup
  for name in common.sh backup.sh restore.sh maintenance.sh; do
    install -m 0755 "$SOURCE/ops/backup/$name" "/usr/local/lib/mag-backup/$name"
  done
  printf '%s\n' "$RELEASE_ID" > /run/mag-backup-release
  timeout --kill-after=10s "${MAG_BACKUP_BUDGET_S:-300}s" systemctl start mag-backup-deploy.service ||
    warn 'pre-deploy backup snapshot failed (journalctl -u mag-backup-deploy)'
else
  warn 'backup not installed (ops/backup/install.sh)'
fi
snapshot() { [[ ! -e $1 ]] || cp -a "$1" "$BACKUP/$2"; }
snapshot $UNITS/mag-120.service mag-120.service
snapshot $UNITS/mag-120.service.d mag-120.service.d
snapshot $UNITS/mag-browser-webrtc.service mag-browser-webrtc.service
snapshot $UNITS/mag-audit-gateway.service mag-audit-gateway.service
snapshot /opt/mag-audit/bin/mag-audit-gateway mag-audit-gateway.bin
snapshot /usr/local/bin/magbot magbot
snapshot "$STATE" room
[[ ! -L /opt/mag-stack/current ]] || readlink /opt/mag-stack/current > "$BACKUP/previous-release"
install -m 0700 "$NGINX_SYNC" "$BACKUP/deploy-nginx.sh"

# ------------------------------------------------------------ 3. stage release
install -d -m 0755 "$RELEASE/bin" "$RELEASE/web" "$RELEASE/ops"
for binary in "${MAG_RELEASE_BINARIES[@]}" mag-browser-webrtc; do
  install -m 0755 "$SOURCE/build/$binary" "$RELEASE/bin/"
done
# The page, its scripts and the few vendored libraries are all in the repo:
# a release works offline and never depends on a CDN or a download.
cp -a "$SOURCE/mag-room/web/." "$RELEASE/web/"
cp -a "$SOURCE/ops/." "$RELEASE/ops/"
find "$RELEASE/ops" -type d \( -name '__pycache__' -o -name 'perf-results' \) -prune -exec rm -rf {} +
(cd "$RELEASE"; find bin web ops -type f -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS)
git -c safe.directory="$SOURCE" -C "$SOURCE" rev-parse HEAD > "$RELEASE/SOURCE_COMMIT"
chown -R root:root "$RELEASE"
chmod -R a+rX "$RELEASE"

# ------------------------------------------------------------------ 4. switch
rollback() {
  echo 'deploy: FAILED; restoring the previous release' >&2
  if (( phase >= 2 )); then
    restore() { if [[ -e $BACKUP/$1 ]]; then rm -rf "$2"; cp -a "$BACKUP/$1" "$2"; fi; }
    restore mag-120.service $UNITS/mag-120.service
    restore mag-120.service.d $UNITS/mag-120.service.d
    restore mag-browser-webrtc.service $UNITS/mag-browser-webrtc.service
    restore mag-audit-gateway.service $UNITS/mag-audit-gateway.service
    restore mag-audit-gateway.bin /opt/mag-audit/bin/mag-audit-gateway
    restore magbot /usr/local/bin/magbot
    if [[ -f $BACKUP/previous-release ]]; then
      ln -sfn "$(cat "$BACKUP/previous-release")" /opt/mag-stack/.current-rollback
      mv -Tf /opt/mag-stack/.current-rollback /opt/mag-stack/current
    fi
    systemctl daemon-reload || :
    systemctl restart mag-audit-gateway.service || :
    systemctl restart mag-120.service mag-browser-webrtc.service || :
  fi
  MAG_NGINX_TXN="$BACKUP" bash "$NGINX_SYNC" rollback || :
  if (( phase >= 2 )); then
    wait_http "$ROOM/readyz" 30 || echo 'deploy: previous room did not come back; see journalctl -u mag-120' >&2
  fi
}

export MAG_NGINX_TXN="$BACKUP"
phase=1
bash "$NGINX_SYNC" stage
phase=2
ln -sfn "$RELEASE" /opt/mag-stack/.current-next
mv -Tf /opt/mag-stack/.current-next /opt/mag-stack/current
# Guest helpers and their narrow sudo rule (the room runs as magbot).
install -m 0755 "$RELEASE/bin/magbot-native" /usr/local/bin/magbot
install -m 0755 "$SOURCE/ops/spawnbot" "$SOURCE/ops/despawnbot" /usr/local/bin/
install -m 0755 "$SOURCE/ops/magbot-guest-run" "$SOURCE/ops/magbot-agent-ctl" "$SOURCE/ops/magbot-netmode" /usr/local/sbin/
# Creating a bot VM gives it a Gitea account through these helpers.
install -m 0755 "$SOURCE/ops/gitea-ci/bot-auth.sh" /usr/local/sbin/mag-gitea-bot-auth
install -m 0755 "$SOURCE/ops/gitea/bot-rights.sh" /usr/local/sbin/mag-gitea-bot-rights
install -D -m 0644 "$SOURCE/ops/gitea-ci/admin-session.sh" /usr/local/lib/mag-ci/admin-session.sh
rm -f /usr/local/sbin/magbot-modelctl
install -m 0440 "$SOURCE/ops/magbot-guest.sudoers" /etc/sudoers.d/.magbot-guest.next
visudo -cf /etc/sudoers.d/.magbot-guest.next >/dev/null
mv -f /etc/sudoers.d/.magbot-guest.next /etc/sudoers.d/magbot-guest
install -m 0644 "$SOURCE/ops/mag-120.service" "$SOURCE/ops/mag-browser-webrtc.service" \
  "$SOURCE/ops/mag-stack-check.service" "$SOURCE/ops/mag-stack-check.timer" \
  "$SOURCE/ops/mag-agents-network.service" "$SOURCE/ops/mag-git-gateway.socket" \
  "$SOURCE/ops/mag-git-gateway.service" "$SOURCE/ops/vm/mag-bot-saves.service" \
  "$SOURCE/ops/vm/mag-bot-saves.timer" $UNITS/
# The room unit is complete in one file; drop-ins of earlier releases (model
# layouts, context budgets) no longer apply. The snapshot above restores them
# on a rollback.
rm -rf $UNITS/mag-120.service.d
# The audit gateway is the only path from the room to the model servers.
gateway_changed=0
if ! cmp -s "$SOURCE/build/mag-audit-gateway" /opt/mag-audit/bin/mag-audit-gateway ||
   ! cmp -s "$SOURCE/ops/audit/systemd/mag-audit-gateway.service" $UNITS/mag-audit-gateway.service; then
  gateway_changed=1
  install -d -m 0755 /opt/mag-audit/bin
  install -m 0755 "$SOURCE/build/mag-audit-gateway" /opt/mag-audit/bin/.mag-audit-gateway.next
  mv -f /opt/mag-audit/bin/.mag-audit-gateway.next /opt/mag-audit/bin/mag-audit-gateway
  install -m 0644 "$SOURCE/ops/audit/systemd/mag-audit-gateway.service" $UNITS/mag-audit-gateway.service
fi
install -m 0644 "$SOURCE/ops/boot/mag-cpu-tune.service" $UNITS/mag-cpu-tune.service
# The self-updater of earlier versions is gone: releases are deployed on purpose.
if [[ -e $UNITS/mag-auto-update.timer || -e $UNITS/mag-auto-update.service ]]; then
  systemctl disable --now mag-auto-update.timer >/dev/null 2>&1 || :
  rm -rf $UNITS/mag-auto-update.timer $UNITS/mag-auto-update.service $UNITS/mag-auto-update.service.d
fi
# Backup jobs must stay off the model cores (Noah owns 14-27).
for unit in "$SOURCE"/ops/backup/units/*.service; do
  [[ ! -e $UNITS/$(basename "$unit") ]] || install -m 0644 "$unit" "$UNITS/$(basename "$unit")"
done
systemctl daemon-reload
systemctl enable mag-120.service mag-browser-webrtc.service >/dev/null 2>&1 || :
# VM isolation rules come from the release (ops/firewall.sh, mag-agents.nft).
systemctl enable mag-agents-network.service >/dev/null 2>&1 || :
systemctl restart mag-agents-network.service
if (( gateway_changed )) && systemctl cat mag-audit-gateway.service >/dev/null 2>&1; then
  systemctl restart mag-audit-gateway.service
  wait_http http://127.0.0.1:8080/audit/v1/health 20 || { echo 'deploy: audit gateway did not start' >&2; exit 1; }
fi
systemctl restart mag-120.service
systemctl restart mag-browser-webrtc.service
wait_http "$ROOM/readyz" 60 || { echo 'deploy: room did not answer /readyz' >&2; exit 1; }
soft wait_http http://127.0.0.1:18784/health 20
bash "$NGINX_SYNC" activate
bash "$NGINX_SYNC" smoke
bash "$NGINX_SYNC" commit
# The release is live and verified: nothing below may roll it back.
phase=3
log "release $RELEASE_ID is live"

# ------------------------------------------------------ 5. independent layers
# Model layer: installs/refreshes the supervisor only. It never restarts a
# running model; a changed launch spec is reported as "restart pending".
soft bash "$SOURCE/ops/models/install.sh"
# Documents in the chat are read for the bots with pdftotext; without it a
# PDF is only a file name to them.
command -v pdftotext >/dev/null || warn 'pdftotext missing: the bots cannot read PDFs (apt-get install poppler-utils)'
# Speech to text (mag-stt): built against the pinned recognizer when it is
# there, restarted only when its binary, model or unit changed.
soft bash "$SOURCE/ops/stt/install.sh"
# Voices for reading aloud (they run in the browser; the room hands them out).
soft bash "$SOURCE/ops/tts/install.sh"
# Scenes (mag-scene): the pinned Blender, on the CPU. A film that is being
# rendered goes on through the worker's restart.
soft bash "$SOURCE/ops/scene/install.sh"
# Optional Idol worker (photo to 3D on the MI50).
if [[ -x /home/chris/idol-mi50/venv/bin/python && -d /home/chris/idol-mi50/weights/idol ]]; then
  soft bash "$SOURCE/ops/idol/install.sh"
fi
# The bots' sites (<name>.bot.stardenburg.de): vhost and publisher.
soft bash "$SOURCE/ops/bots/install.sh"
soft systemctl enable --now mag-stack-check.timer
# Every bot VM is saved each night (magbot save-all; the last four per bot).
soft systemctl enable --now mag-bot-saves.timer
soft bash "$SOURCE/ops/boot/ensure-boot.sh"
MAG_RELEASE_ID="$RELEASE_ID" MAG_RELEASE_PREVIOUS_FILE="$BACKUP/previous-release" \
  soft bash "$SOURCE/ops/prune-releases.sh"
printf '\nInstalled release %s (commit %s); rollback: sudo bash ops/rollback.sh %s\n' \
  "$RELEASE_ID" "$(cut -c1-12 "$RELEASE/SOURCE_COMMIT")" "$RELEASE_ID"
