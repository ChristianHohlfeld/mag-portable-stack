#!/usr/bin/env bash
# Run as root after building and checking the native source.
set -euo pipefail
# shellcheck source=deploy-guard.sh
source "$(cd -- "$(dirname -- "$0")" && pwd)/deploy-guard.sh"
mag_deploy_arm_deadline "$@"
SOURCE="$(cd -- "$(dirname -- "$0")/.." && pwd)"
RELEASE_ID="${MAG_RELEASE_ID:-$(date -u +%Y%m%dT%H%M%SZ)}"
RELEASE="/opt/mag-stack/releases/$RELEASE_ID"
BACKUP="/var/backups/mag-stack/$RELEASE_ID"
WHISPER_CLI="${MAG_WHISPER_SOURCE:-/home/chris/.cache/mag-stack/whisper-build/bin/whisper-cli}"
WHISPER_MODEL="${MAG_WHISPER_SOURCE_MODEL:-/home/chris/models/whisper/ggml-large-v3-turbo.bin}"
[[ $EUID == 0 ]] || { echo 'Run with sudo' >&2; exit 1; }
exec 8>/run/lock/mag-private-deploy.lock
flock -n 8 || { echo 'Another MAG deploy/ingress install is running' >&2; exit 1; }
# shellcheck source=deploy-watchdog.sh
source "$SOURCE/ops/deploy-watchdog.sh"
deploy_lock_cloexec 8
deployment_started=0
# Drain budget plus 15 minutes. A hung probe cannot hold this flock past the
# deadline: its process group is killed, then SIGTERM runs deploy_exit.
_deploy_drain=${MAG_DEPLOY_DRAIN_S:-900}
deploy_watchdog_arm "${MAG_DEPLOY_DEADLINE_S:-$((_deploy_drain + 900))}"
trap deploy_exit EXIT
trap 'exit 143' TERM
trap 'exit 130' INT
[[ "$RELEASE_ID" =~ ^[a-zA-Z0-9._-]+$ ]] || exit 1
# shellcheck source=build-targets.sh
source "$SOURCE/ops/build-targets.sh"
for binary in "${MAG_RELEASE_BINARIES[@]}"; do test -x "$SOURCE/build/$binary"; done
# Go is used only for WebRTC/UDP; the room, browsers and VM lifecycle remain C++.
if [[ ! -x "$SOURCE/build/mag-browser-webrtc" ]]; then
  command -v go >/dev/null || { echo 'Go >=1.23 required to build browser WebRTC gateway' >&2; exit 1; }
  (cd "$SOURCE/mag-room/webrtc" && go build -mod=mod -o "$SOURCE/build/mag-browser-webrtc" .)
fi
test -x "$SOURCE/build/mag-browser-webrtc"
test -x "$WHISPER_CLI" && test -r "$WHISPER_MODEL" || {
  echo "Whisper missing ($WHISPER_CLI, $WHISPER_MODEL): run ops/bootstrap-voice.sh as the checkout owner" >&2; exit 1;
}
test ! -e "$RELEASE"
# First install on a blank host (ops/bootstrap.sh): there is no previous
# release, room unit, magbot binary or room state to snapshot, and no running
# room whose native whois could be probed. Later deploys are unchanged.
FIRST_INSTALL=0
[[ -L /opt/mag-stack/current ]] || FIRST_INSTALL=1
MAG_DEPLOY_FIRST_INSTALL=$FIRST_INSTALL; export MAG_DEPLOY_FIRST_INSTALL
# No release state mutation before complete nginx/Tailnet/Owner-probe validation.
NGINX_SYNC="$SOURCE/ops/private-access/deploy-nginx.sh"
bash "$NGINX_SYNC" preflight
# Fail-closed synchronous encrypted snapshot, only AFTER the read-only #36/#38
# nginx/Whois/Owner preflight and BEFORE any release or service changes.
mag_systemctl is-enabled --quiet mag-backup-daily.timer || {
  echo 'Backup not installed/enabled. Run sudo bash ops/backup/install.sh first.' >&2; exit 1;
}
# The unit runs the installed copy. Refresh it first so the snapshot is a
# verified full archive under /var/backups/mag-full (keep the last 3).
install -d -m 0755 /usr/local/lib/mag-backup
for name in common.sh backup.sh restore.sh maintenance.sh; do
  install -m 0755 "$SOURCE/ops/backup/$name" "/usr/local/lib/mag-backup/$name"
done
printf '%s\n' "$RELEASE_ID" > /run/mag-backup-release
chmod 0644 /run/mag-backup-release
# Retry a failed/stale unit with the current script, but never accept an old
# VERIFIED marker as proof that this deployment's snapshot succeeded.
if ! mag_systemctl start mag-backup-deploy.service; then
  echo 'Backup unit failed; trying the installed backup script.' >&2
  /usr/local/lib/mag-backup/backup.sh deploy || {
    echo 'Pre-deploy snapshot failed; no release switched.' >&2; exit 1;
  }
fi
verified_full=$(find /var/backups/mag-full -mindepth 2 -maxdepth 2 -name VERIFIED -print -quit 2>/dev/null || true)
if [[ -z "$verified_full" ]]; then
  /usr/local/lib/mag-backup/backup.sh deploy
  verified_full=$(find /var/backups/mag-full -mindepth 2 -maxdepth 2 -name VERIFIED -print -quit 2>/dev/null || true)
fi
[[ -n "$verified_full" ]] || { echo 'No verified full backup; deploy refused.' >&2; exit 1; }
# Das Modell ist unabhängig vom Raum-Release (lädt evtl. noch herunter / wird geprüft).
# Nur informieren; der Raum zeigt den Modellstatus selbst an.
deploy_run 8 -- curl -fsS --retry 0 --max-time 5 http://127.0.0.1:8080/v1/models >/dev/null \
  || echo 'Hinweis: Modell-Backend noch nicht bereit – Deploy läuft weiter.' >&2
install -d -m 0755 /opt/mag-stack/releases "$RELEASE/bin" "$RELEASE/web" "$RELEASE/ops"
install -d -m 0700 "$BACKUP"
install -m 0700 "$NGINX_SYNC" "$BACKUP/deploy-nginx.sh"
if [[ -f /etc/systemd/system/mag-120.service ]]; then
  cp -a /etc/systemd/system/mag-120.service "$BACKUP/mag-120.service"
fi
for config in room-model.env secondary-model.env; do
  [[ ! -f /etc/mag-stack/$config ]] || cp -a "/etc/mag-stack/$config" "$BACKUP/$config"
done
# Gateway binary and unit roll back together with the room.
if [[ -f /etc/systemd/system/mag-audit-gateway.service ]]; then
  cp -a /etc/systemd/system/mag-audit-gateway.service "$BACKUP/mag-audit-gateway.service"
fi
if [[ -x /opt/mag-audit/bin/mag-audit-gateway ]]; then
  cp -a /opt/mag-audit/bin/mag-audit-gateway "$BACKUP/mag-audit-gateway.bin"
fi
if [[ -f /etc/systemd/system/mag-browser-webrtc.service ]]; then
  cp -a /etc/systemd/system/mag-browser-webrtc.service "$BACKUP/mag-browser-webrtc.service"
fi
if [[ -e /usr/local/bin/magbot ]]; then cp -a /usr/local/bin/magbot "$BACKUP/magbot"
elif (( ! FIRST_INSTALL )); then echo '/usr/local/bin/magbot missing on an installed host' >&2; exit 1; fi
if [[ -d /var/lib/magbot/room ]]; then cp -a /var/lib/magbot/room "$BACKUP/room"
elif (( ! FIRST_INSTALL )); then echo '/var/lib/magbot/room missing on an installed host' >&2; exit 1; fi
if [[ -L /opt/mag-stack/current ]]; then readlink /opt/mag-stack/current > "$BACKUP/previous-release"; fi
install -m 0755 "$SOURCE/build/magroom" "$SOURCE/build/mag-browser" "$SOURCE/build/magbot-native" "$SOURCE/build/mag-bots-publish" "$SOURCE/build/mag-browser-webrtc" "$SOURCE/build/mag-det" "$WHISPER_CLI" "$RELEASE/bin/"
cp -a "$SOURCE/mag-room/web/." "$RELEASE/web/"
# Long MCP names wrap. The page is applied here so the source file stays the
# one CI already syntax-checks, plus this one reviewed diff.
patch --forward --batch "$RELEASE/web/index.html" \
  "$SOURCE/ops/trust/mcp-card-overflow.patch"
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
# Piper read-aloud voices (CC0). Optional: without them the room falls back
# to the best system voice, so a failed download never blocks a deploy.
if bash "$SOURCE/ops/vendor-tts-assets.sh" "$SOURCE/build/tts-assets"; then
  cp -a "$SOURCE/build/tts-assets/." "$RELEASE/web/static/"
else
  echo "WARN: Piper TTS assets unavailable; read-aloud uses system voices" >&2
fi
cp -a "$SOURCE/ops/." "$RELEASE/ops/"
find "$RELEASE/ops" -type d -name '__pycache__' -prune -exec rm -rf {} +
find "$RELEASE/ops" -name '*.pyc' -delete
install -d -o magbot -g magbot -m 0750 /var/lib/mag-stack/models
install -o magbot -g magbot -m 0640 "$WHISPER_MODEL" /var/lib/mag-stack/models/ggml-large-v3-turbo.bin
# The room UI comes only from the release. A bot-published override (the
# removed publish_frontend tool) is moved aside next to itself, never deleted.
if [[ -f /var/lib/magbot/room/frontend.html ]]; then
 mv /var/lib/magbot/room/frontend.html "/var/lib/magbot/room/frontend.html.bak-$(date +%Y%m%d%H%M%S)"
fi
(cd "$RELEASE"; find bin web ops -type f -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS)
# Root reads the user's checkout; without sudo's SUDO_UID (timer) git refuses it.
git -c safe.directory="$SOURCE" -C "$SOURCE" rev-parse HEAD > "$RELEASE/SOURCE_COMMIT"
chown -R root:root "$RELEASE"
chmod -R a+rX "$RELEASE"
# All changes after this point must unwind both the native room and nginx.
# Only the explicit NUMA1 layout transaction may switch a model.
rollback() {
 echo 'Deployment failed; restoring previous room and nginx state' >&2
 if (( room_switched )); then
 if [[ -f "$BACKUP/mag-120.service" ]]; then
   cp -a "$BACKUP/mag-120.service" /etc/systemd/system/mag-120.service
 else
   mag_systemctl disable --now mag-120.service >/dev/null 2>&1 || true
   rm -f /etc/systemd/system/mag-120.service
 fi
 if [[ -f "$BACKUP/mag-browser-webrtc.service" ]]; then
   cp -a "$BACKUP/mag-browser-webrtc.service" /etc/systemd/system/mag-browser-webrtc.service
 else
   mag_systemctl disable --now mag-browser-webrtc.service >/dev/null 2>&1 || true
   rm -f /etc/systemd/system/mag-browser-webrtc.service
 fi
 if [[ -e "$BACKUP/magbot" ]]; then cp -a "$BACKUP/magbot" /usr/local/bin/magbot; else rm -f /usr/local/bin/magbot; fi
 if [[ -f "$BACKUP/previous-release" ]]; then
   ln -sfn "$(cat "$BACKUP/previous-release")" /opt/mag-stack/.previous-rollback
   mv -Tf /opt/mag-stack/.previous-rollback /opt/mag-stack/current
 else
   rm -f /opt/mag-stack/current
 fi
 if [[ -d "$BACKUP/mag-120.service.d" ]]; then
   rm -rf /etc/systemd/system/mag-120.service.d
   cp -a "$BACKUP/mag-120.service.d" /etc/systemd/system/mag-120.service.d
 else
   rm -f /etc/systemd/system/mag-120.service.d/30-multibot.conf /etc/systemd/system/mag-120.service.d/30-dual-numa.conf
 fi
 for config in room-model.env secondary-model.env; do
   if [[ -f $BACKUP/$config ]]; then cp -a "$BACKUP/$config" "/etc/mag-stack/$config"
   else rm -f "/etc/mag-stack/$config"; fi
 done
 if [[ -f $BACKUP/qwen-layout/helper.sh ]]; then
   bash "$BACKUP/qwen-layout/helper.sh" rollback "$BACKUP"
 fi
 if (( gateway_switched )); then
   if [[ -f "$BACKUP/mag-audit-gateway.service" ]]; then
     cp -a "$BACKUP/mag-audit-gateway.service" /etc/systemd/system/mag-audit-gateway.service
   fi
   if [[ -f "$BACKUP/mag-audit-gateway.bin" ]]; then
     install -m 0755 "$BACKUP/mag-audit-gateway.bin" /opt/mag-audit/bin/.mag-audit-gateway.restore
     mv -f /opt/mag-audit/bin/.mag-audit-gateway.restore /opt/mag-audit/bin/mag-audit-gateway
   fi
 fi
 mag_systemctl daemon-reload
 if (( gateway_switched )); then mag_systemctl restart mag-audit-gateway.service || true; fi
 if [[ -f "$BACKUP/mag-120.service" ]]; then mag_systemctl restart mag-120.service; fi
 if [[ -f "$BACKUP/mag-browser-webrtc.service" ]]; then
   mag_systemctl restart mag-browser-webrtc.service
 fi
 fi
 rm -f /opt/mag-stack/.current-next
 # Release tailscaled :443 before nginx tries to bind the Tailscale address.
 if [[ -d $BACKUP/funnel-snapshot ]]; then
   MAG_NGINX_TXN="$BACKUP" bash "$SOURCE/ops/tailscale/restore-gitea-funnel.sh" \
     || echo 'WARN: funnel listener restore incomplete' >&2
 fi
 bash "$NGINX_SYNC" rollback
}
deployment_started=0
room_switched=0
gateway_switched=0
# Units are Type=simple: `mag_systemctl restart` returns before the process
# listens. Retry a local health URL for up to 30 s, then one strict check.
wait_http() {
  local i
  for i in {1..30}; do
    deploy_run 4 -- curl -fsS --retry 0 --max-time 2 -o /dev/null "$1" && break
    sleep 1
  done
  deploy_run 8 -- curl -fsS --retry 0 --max-time 5 "$1"
}
# Idle room, or a busy flag that is not producing tokens. A stuck busy=true
# (no prompt/output progress) must not sit out the whole drain window.
# MAG_DEPLOY_STALE_S=0 disables the stale shortcut. Returns 75 to defer.
drain_room() {
  local drain_s="${MAG_DEPLOY_DRAIN_S:-900}" stale_s="${MAG_DEPLOY_STALE_S:-60}"
  local waited=0 still=0 prev="" st busy=0 queued=0 progress=""
  while :; do
    st=$(curl -fsS --max-time 5 http://127.0.0.1:8766/api/state 2>/dev/null \
         | python3 -c 'import json,sys
try:
 d=json.load(sys.stdin)
except Exception:
 print("0 0 -"); raise SystemExit
b=1 if d.get("busy") else 0
q=0 if d.get("queue_paused") else int(d.get("queue") or 0)
bits=[]
for a in d.get("activities") or []:
 if isinstance(a,dict): bits.append("%s/%s/%s/%s"%(a.get("processed"),a.get("total"),a.get("phase"),len(a.get("steps") or [])))
n=0
for m in d.get("room") or []:
 if isinstance(m,dict): n+=len(m.get("content") or "")+len(m.get("reasoning") or "")
print("%d %d p%s:t%d"%(b,q,",".join(bits),n))' || echo "0 0 -")
    read -r busy queued progress <<<"$st"
    [[ "$busy" == 0 && "$queued" == 0 ]] && return 0
    if [[ "$progress" == "$prev" ]]; then still=$((still + 5)); else still=0; prev=$progress; fi
    if (( waited == 0 )); then echo "Deploy wartet: Antwort läuft ($busy) / $queued in der Warteschlange – warte bis max. ${drain_s}s …" >&2; fi
    if (( busy == 1 && stale_s > 0 && still >= stale_s )); then
      echo "Deploy: busy ohne neue Tokens seit ${still}s – Deploy läuft weiter (Warteschlange bleibt gespeichert)." >&2
      return 0
    fi
    if (( waited >= drain_s )); then
      if [[ "${MAG_DEPLOY_FORCE:-0}" == 1 ]]; then echo "Drain-Timeout – MAG_DEPLOY_FORCE=1: deploye trotzdem (Warteschlange wird nach dem Neustart fortgesetzt)." >&2; return 0; fi
      echo "Drain-Timeout nach ${drain_s}s: Raum ist nicht leer – Deploy verschoben (Release $RELEASE_ID bleibt unbenutzt). Später erneut ausführen oder MAG_DEPLOY_FORCE=1." >&2
      return 75
    fi
    sleep 5; waited=$((waited + 5))
  done
}
# deploy_exit lives in deploy-watchdog.sh: stop the deadline sleeper, then
# roll back when this shell is signalled after the release switch started.

# Drain: never cut a running reply or a queued message. Wait until the room is
# idle (no turn running, queue empty). MAG_DEPLOY_DRAIN_S (default 900 s);
# on timeout the deploy is deferred (exit 75) unless MAG_DEPLOY_FORCE=1.
# A busy flag with no token progress for MAG_DEPLOY_STALE_S (default 60 s)
# is stale: continue. The queue is persisted and resumed after the restart.
if ! drain_room; then
  rc=$?
  if (( rc == 75 )); then rm -rf "$RELEASE"; exit 75; fi
  exit "$rc"
fi
# A changed nginx/whois config needs the Owner smoke from a second Tailnet
# device; otherwise smoke is the local 443 ingress check (deploy-nginx.sh).
# nginx.stage snapshots the old vhost and validates the candidate BEFORE
# switching symlinks or restarting the room.
MAG_NGINX_TXN="$BACKUP"; export MAG_NGINX_TXN
deployment_started=1
trap deploy_exit EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
bash "$NGINX_SYNC" stage
# Retire the old installer timer before changing its service executable.
if mag_systemctl cat mag-120-finalize.timer >/dev/null 2>&1; then mag_systemctl disable --now mag-120-finalize.timer; fi
room_switched=1
ln -sfn "$RELEASE" /opt/mag-stack/.current-next
mv -Tf /opt/mag-stack/.current-next /opt/mag-stack/current
# Compatibility name is deliberately retained for existing guest wrappers/sudoers.
install -m 0755 "$RELEASE/bin/magbot-native" /usr/local/bin/magbot
install -m 0755 "$SOURCE/ops/spawnbot" "$SOURCE/ops/despawnbot" /usr/local/bin/
install -m 0755 "$SOURCE/ops/magbot-guest-run" "$SOURCE/ops/magbot-agent-ctl" "$SOURCE/ops/magbot-netmode" /usr/local/sbin/
install -m 0440 "$SOURCE/ops/magbot-guest.sudoers" /etc/sudoers.d/magbot-guest
visudo -cf /etc/sudoers.d/magbot-guest >/dev/null
install -m 0644 "$SOURCE/ops/mag-120.service" "$SOURCE/ops/mag-browser-webrtc.service" /etc/systemd/system/
# systemd warns "mag-120.service changed on disk" if enable/start/restart
# runs against a unit file that was not daemon-reloaded.
mag_systemctl daemon-reload
install -m 0644 "$SOURCE/ops/mag-stack-check.service" "$SOURCE/ops/mag-stack-check.timer" "$SOURCE/ops/mag-git-gateway.socket" "$SOURCE/ops/mag-git-gateway.service" "$SOURCE/ops/mag-agents-network.service" /etc/systemd/system/
# Read-only backing image; existing VM overlays remain untouched. A host
# without the image (ops/vm/build-golden.sh not run yet) still gets the room;
# only bot creation is refused by the native preflight until it exists.
if [[ -f /var/lib/libvirt/images/magbot/base/golden.qcow2 ]]; then
  chmod 0440 /var/lib/libvirt/images/magbot/base/golden.qcow2
else
  echo 'WARN: golden.qcow2 missing; build it with: sudo bash ops/vm/build-golden.sh' >&2
fi
# Replace a drop-in that still points at an old release before pruning.
# Snapshot first so a failed smoke restores the previous unit drop-ins.
if [[ -d /etc/systemd/system/mag-120.service.d ]]; then
  cp -a /etc/systemd/system/mag-120.service.d "$BACKUP/mag-120.service.d"
fi
audit_drop=/etc/systemd/system/mag-120.service.d/20-audit.conf
if [[ -L "$audit_drop" ]]; then
  audit_target=$(readlink -f "$audit_drop" || true)
  case "$audit_target" in
    /opt/mag-stack/releases/*)
      install -d -m 0755 /etc/systemd/system/mag-120.service.d
      install -m 0644 "$SOURCE/ops/mag-120.d/20-room-audit.conf" \
        /etc/systemd/system/mag-120.service.d/20-room-audit.conf
      rm -f "$audit_drop"
      ;;
  esac
fi
# Keep the existing single-bot room for legacy IQ3 installs. On a verified
install -d -m 0755 /etc/systemd/system/mag-120.service.d
install -m 0644 "$SOURCE/ops/mag-120.d/40-hardening.conf" /etc/systemd/system/mag-120.service.d/40-hardening.conf
# Gitea bot accounts: never admin, restricted, own org bots-<agent> (idempotent).
install -m 0755 "$SOURCE/ops/gitea/bot-rights.sh" /usr/local/sbin/mag-gitea-bot-rights
install -d -m 0755 /usr/local/lib/mag-ci
install -m 0644 "$SOURCE/ops/gitea-ci/admin-session.sh" /usr/local/lib/mag-ci/admin-session.sh
install -m 0755 "$SOURCE/ops/gitea-ci/bot-auth.sh" /usr/local/sbin/mag-gitea-bot-auth
if mag_systemctl is-active --quiet gitea.service; then
  /usr/local/sbin/mag-gitea-bot-rights || echo 'WARN: Gitea bot rights not applied' >&2
fi
# Qwen host, use exactly two model-pinned bots with separate KVM workspaces.
rm -f /etc/systemd/system/mag-120.service.d/30-multibot.conf
if [[ -e /etc/mag-stack/room-model.env || -e /etc/systemd/system/mag-llm-ds-node@.service ]]; then
  # The layout helper owns its own NUMA1 snapshot, including config and boot state.
  # Stop the drained room while switching so no new turn reaches the old router.
  if (( ! FIRST_INSTALL )); then
    mag_systemctl stop mag-120.service
    bash "$SOURCE/ops/qwen/layout.sh" apply "$BACKUP"
  fi
  bash "$SOURCE/ops/room/install.sh"
elif [[ -r /etc/mag-stack/secondary-model.env ]] &&
   grep -Fxq 'MAG_SECONDARY_MODEL=ornith' /etc/mag-stack/secondary-model.env; then
  echo 'Ornith cutover required: stop Ornith, then run ops/room/install.sh before deploy' >&2
  exit 1
elif [[ -r /etc/mag-stack/secondary-model.env ]] &&
   grep -Fxq 'MAG_SECONDARY_MODEL=qwen' /etc/mag-stack/secondary-model.env; then
  install -d -m 0755 /etc/systemd/system/mag-120.service.d /usr/local/lib/mag-qwen
  install -m 0644 "$SOURCE/ops/mag-120.d/30-dual-numa.conf" /etc/systemd/system/mag-120.service.d/30-dual-numa.conf
  # Update only the on-disk systemd definition; NEVER restart either loaded LLM.
  [[ -x /usr/local/lib/mag-qwen/start.sh ]] || { echo 'Qwen staged runtime missing' >&2; exit 1; }
  install -m 0755 "$SOURCE/ops/qwen/start.sh" /usr/local/lib/mag-qwen/start.sh
  install -m 0755 "$SOURCE/ops/qwen/verify-model.sh" /usr/local/lib/mag-qwen/verify-model.sh
  install -m 0644 "$SOURCE/ops/qwen/mag-llm-qwen.service" /etc/systemd/system/mag-llm-qwen.service
  # Remove obsolete Q4 preflight; Qwen starts independently.
  rm -f /etc/systemd/system/mag-llm-qwen.service.d/20-q4-health.conf
else
  rm -f /etc/systemd/system/mag-120.service.d/30-dual-numa.conf
fi
# Replace the live gateway in this deployment, never leave an old :8090 router behind.
test -x "$SOURCE/build/mag-audit-gateway" || { echo 'Native gateway binary missing' >&2; exit 1; }
gateway_switched=1
install -m 0644 "$SOURCE/ops/audit/systemd/mag-audit-gateway.service" /etc/systemd/system/mag-audit-gateway.service
install -d -m 0755 /opt/mag-audit/bin
install -m 0755 "$SOURCE/build/mag-audit-gateway" /opt/mag-audit/bin/.mag-audit-gateway.next
mv -f /opt/mag-audit/bin/.mag-audit-gateway.next /opt/mag-audit/bin/mag-audit-gateway
mag_systemctl daemon-reload
mag_systemctl restart mag-audit-gateway.service
# Type=simple returns before the new gateway has bound :8080. Use the same
# bounded readiness retry as the room/RTC smoke checks (also on a blank host).
gateway_models=$(wait_http http://127.0.0.1:8080/v1/models)
if [[ -r /etc/mag-stack/room-model.env ]]; then
  python3 -I -c 'import json,sys,shlex
v={}
for line in open(sys.argv[2]):
 parts=shlex.split(line,comments=True)
 if parts and "=" in parts[0]:
  k,value=parts[0].split("=",1);v[k]=value
expected={v.get("MAG_ROOM_MODEL_ID_"+str(n),v["MAG_ROOM_MODEL_ID"]) for n in (0,1)}
assert {m["id"] for m in json.loads(sys.argv[1])["data"]} == expected, "gateway model mismatch"
' "$gateway_models" /etc/mag-stack/room-model.env
elif [[ -r /etc/mag-stack/secondary-model.env ]]; then
  case $(cat /etc/mag-stack/secondary-model.env) in
    MAG_SECONDARY_MODEL=ornith) required=ornith-1.5-35b-a3b ;;
    MAG_SECONDARY_MODEL=qwen) required=qwen3.8-flash-next-uncensored ;;
    *) required='' ;;
  esac
  if [[ -n $required ]]; then
    python3 -I -c 'import json,sys
ids=[m["id"] for m in json.loads(sys.argv[1])["data"]]
assert ids == [sys.argv[2]], "gateway exposes retired model"
' "$gateway_models" "$required" || { echo 'Gateway model route mismatch; rollback' >&2; exit 1; }
  fi
fi
mag_systemctl enable --now mag-agents-network.service
mag_systemctl restart mag-agents-network.service
mag_systemctl daemon-reload
mag_systemctl enable mag-120.service
# Switch nginx while the previous native whois backend is still available.
# A failed ingress check rolls back BEFORE replacing the running room process.
# First install (blank host): there is no previous room to answer nginx's
# whois subrequest, so the new room starts first (rollback disables it).
if ! mag_systemctl is-active --quiet mag-120.service; then
  mag_systemctl start mag-120.service
  for i in {1..30}; do deploy_run 4 -- curl -fsS --retry 0 --max-time 2 -o /dev/null http://127.0.0.1:8766/readyz && break; sleep 1; done
fi
# One owner of Tailscale :443. nginx leaves 100.123.114.16:443 for
# tailscaled, which Funnel-proxies https://mag.tail3e02ae.ts.net/ to
# http://127.0.0.1:3000. A failed apply before that move restores nginx.
bash "$SOURCE/ops/tailscale/apply-gitea-funnel.sh"
bash "$NGINX_SYNC" activate
bash "$NGINX_SYNC" smoke
# Public ISRG roots before the room restarts, so MCP HTTPS (and the audit
# proxy's upstream check) trust Let's Encrypt Generation Y. Does not touch
# golden.qcow2 or any model unit.
bash "$SOURCE/ops/trust/install-trust.sh"
# Prove the audited proxy can reach the public Free MCP. nginx is already
# switched and the room process is still the previous healthy one. A 429,
# timeout, or other probe miss must not roll that room back. TLS stays ON.
# Does not restart mag-llm (NUMA 0 stays loaded).
# Own process group. curl --retry sleeps on Retry-After outside --max-time;
# the deadline kills this group so the flock cannot outlive the deploy.
if ! deploy_run "${MAG_FREE_MCP_PROBE_TIMEOUT:-40}" -- bash "$SOURCE/ops/trust/probe-free-mcp.sh"; then
  echo 'WARN: free MCP probe failed; not rolling back a healthy room' >&2
fi
mag_systemctl daemon-reload
mag_systemctl restart mag-120.service
mag_systemctl enable --now mag-browser-webrtc.service
mag_systemctl restart mag-browser-webrtc.service
# /readyz = Raum bereit (Modellstatus steht im JSON, ist aber kein Rollback-Grund)
wait_http http://127.0.0.1:8766/readyz
wait_http http://127.0.0.1:18784/health
# Both NUMA slots must already hold a prefix a first hi can extend in under
# 5s. Keep the release either way: rolling back restarts mag-120, and this
# never restarts mag-llm-ds-node@0.
if ! bash "$SOURCE/ops/deploy-warm-hi.sh"; then
  echo 'WARN: warm hi gate failed; keeping this release and not restarting mag-llm' >&2
fi
# Revalidate the new room through the already-verified nginx ingress.
bash "$NGINX_SYNC" smoke
/usr/local/bin/magbot status
# Running bots keep their overlay. Push the refreshed guest trust store now;
# a later despawn deletes that disk and the anchors with it.
if [[ -d /etc/mag-audit/vm ]]; then
  while read -r id state _; do
    [[ "$state" == ready ]] || continue
    /usr/local/bin/magbot audit-proxy "$id" \
      || echo "WARN: guest trust not applied in $id" >&2
  done < <(/usr/local/bin/magbot status || true)
fi
for unit in mag-chat.service pm2-chris.service; do if mag_systemctl cat "$unit" >/dev/null 2>&1; then mag_systemctl disable --now "$unit"; fi; done
mag_systemctl enable --now mag-stack-check.timer mag-git-gateway.socket
install -m 0644 "$SOURCE/ops/boot/mag-cpu-tune.service" /etc/systemd/system/mag-cpu-tune.service
mag_systemctl daemon-reload
mag_systemctl enable mag-cpu-tune.service
mag_systemctl restart mag-cpu-tune.service || echo 'WARN: CPU tuning not applied' >&2
bash "$NGINX_SYNC" commit
# Late steps must not roll a switched release back. The deadline stays armed
# so a hang here still kills child groups and runs deploy_exit.
deployment_started=0
MAG_RELEASE_ID="$RELEASE_ID" MAG_RELEASE_PREVIOUS_FILE="$BACKUP/previous-release" \
  bash "$SOURCE/ops/prune-releases.sh"
# One-shot removal of pre-4.0 leftovers; report in /var/lib/mag-stack/cleanup-report.txt.
# Backup removal and loose home files run again on later deploys.
bash "$SOURCE/ops/cleanup-legacy.sh" || echo 'WARN: legacy cleanup incomplete (deploy itself succeeded)' >&2
install -m 0755 "$SOURCE/ops/agent-drift.sh" /usr/local/sbin/mag-agent-drift
install -m 0644 "$SOURCE/ops/mag-agent-drift.service" /etc/systemd/system/mag-agent-drift.service
install -m 0644 "$SOURCE/ops/mag-agent-drift.timer" /etc/systemd/system/mag-agent-drift.timer
mag_systemctl daemon-reload
mag_systemctl enable --now mag-agent-drift.timer || echo 'WARN: agent drift timer not enabled' >&2
# Unreferenced half-created bot VMs only. Does not restart a model unit.
bash /usr/local/sbin/mag-agent-drift || echo 'WARN: agent drift check incomplete (deploy itself succeeded)' >&2
# Tailnet name for each bot VM. No-op until the one-time wildcard cert exists.
# Does not restart a model unit. DNS for *.bot.stardenburg.de stays at the registrar.
bash "$SOURCE/ops/bots/install.sh" || echo 'WARN: bot site install incomplete (deploy itself succeeded)' >&2
# DeepSeek is retired (owner decision): only on an Ornith host with 8191+8192
# healthy, disable --now mag-llm/IQ3/model-gate/model-pin. Before ensure-boot so it
# never re-enables mag-llm. Never touches mag-120 or model files.
[[ -r /etc/mag-stack/room-model.env ]] || bash "$SOURCE/ops/models/retire-deepseek.sh" || echo 'WARN: DeepSeek retirement incomplete (deploy itself succeeded)' >&2
# Persist the active model units and bot VM autostart for cold reboot.
bash "$SOURCE/ops/boot/ensure-boot.sh" || echo 'WARN: boot persistence needs operator attention' >&2
# Funnel/serve was applied before the ingress smoke, while rollback could
# still release :443. Model files, including DeepSeek V4.1, are preserved. Only Chris deletes weights.
# Deploy never restarts a loaded model (DeepSeek retirement above is the only stop). Model placement
# is a separate, measured, operator-controlled change, never a deploy hook.
printf '\nInstalled release %s; rollback snapshot %s\n' "$RELEASE_ID" "$BACKUP"


