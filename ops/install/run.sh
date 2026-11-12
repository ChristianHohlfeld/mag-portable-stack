#!/usr/bin/env bash
# MAG on a fresh Ubuntu 24.04 server, from this repository alone (./install.sh).
# Fresh means: no data of a lost host (that is ops/backup/system-restore.sh),
# but everything running: packages, users, kernel settings, Tailscale, TLS,
# nginx, the audit layer, the golden VM image, Gitea and its runner, the model
# runtime and the models (mag-modeld downloads them), speech, voices, Blender,
# the Idol worker with its weights, the room, the bots and their VMs, backups.
#
# Phases run in order; each one that went through is remembered in
# /var/lib/mag-install, so running it again goes on where it stopped.
#   sudo ./install.sh                 every phase not yet done
#   sudo ./install.sh --list          the phases and which are done
#   sudo ./install.sh --only PHASE    one phase (again)
#   sudo ./install.sh --from PHASE    that phase and all after it (again)
#
# Secrets are never in the repository. Where one is needed the phase stops
# with what to put where; the templates are in ops/install/templates and
# docs/INSTALL.md lists them all. Builds and downloads run on CPUs 28-55.
set -euo pipefail
[[ $EUID == 0 ]] || { echo 'Run with sudo: sudo ./install.sh' >&2; exit 1; }
REPO=$(cd -- "$(dirname -- "$(readlink -f "$0")")/../.." && pwd)
OWNER=${MAG_OWNER:-${SUDO_USER:-chris}}
OWNER_HOME=$(getent passwd "$OWNER" | cut -d: -f6 || true)
DONE=/var/lib/mag-install
TPL=$REPO/ops/install/templates
CPUS=${MAG_BUILD_CPUS:-28-55}
install -d -m 0700 "$DONE"
log() { printf '\n== install: %s\n' "$*"; }
note() { printf 'install: %s\n' "$*"; }
stop() { printf '\ninstall: STOPPED in phase %s:\n  %s\nFix that, then run sudo ./install.sh again; it goes on here.\n' "$phase" "$*" >&2; exit 3; }
as_owner() { sudo -u "$OWNER" -H taskset -c "$CPUS" nice -n 10 "$@"; }
pinned() { taskset -c "$CPUS" nice -n 10 "$@"; }

PHASES=(preflight packages users kernel secrets tailscale trust build libvirt audit golden tls gitea runtime media idol deploy bots funnel backup check)

# ------------------------------------------------------------------ phases
p_preflight() {
  . /etc/os-release
  [[ ${ID:-} == ubuntu && ${VERSION_ID:-} == 24.04 ]] || stop "Ubuntu 24.04 is required (this is ${PRETTY_NAME:-unknown})"
  [[ $(uname -m) == x86_64 ]] || stop 'x86_64 is required'
  [[ -n $OWNER_HOME && -d $OWNER_HOME ]] || stop "the owner user '$OWNER' must exist (MAG_OWNER=...); the repo expects /home/$OWNER"
  [[ $OWNER == chris ]] || note "WARN: units and models.json name /home/chris; with owner '$OWNER' adapt them first"
  [[ $REPO == "$OWNER_HOME/mag-stack" ]] || note "WARN: the checkout is $REPO; deploys expect $OWNER_HOME/mag-stack"
  [[ -e /dev/kvm ]] || stop '/dev/kvm is missing: enable virtualisation in the BIOS'
  local nodes cpus
  nodes=$(ls -d /sys/devices/system/node/node[0-9]* 2>/dev/null | wc -l)
  cpus=$(nproc --all)
  (( nodes >= 2 && cpus >= 56 )) || note "WARN: $nodes NUMA nodes, $cpus CPUs; MAG is laid out for 2 nodes and 56 CPUs (0-13 Lena, 14-27 Noah, 28-55 the rest; ops/models/models.json)"
  local free; free=$(df -BG --output=avail / | tail -1 | tr -dc 0-9)
  (( free >= 700 )) || note "WARN: ${free} GB free; the models and weights need about 600 GB"
  if grep -qs 0x66a1 /sys/bus/pci/devices/*/device; then note 'MI50 found (Idol: photo to 3D, pictures, video; power cap 72 W)'
  else note 'no MI50: the Idol worker stays off; everything else works'; fi
}

p_packages() {
  local list
  list=$(grep -v '^#' "$REPO/ops/install/packages.txt" | tr '\n' ' ')
  apt-get update
  # shellcheck disable=SC2086
  DEBIAN_FRONTEND=noninteractive apt-get install -y $list
  snap list go >/dev/null 2>&1 || snap install go --classic
}

p_users() {
  getent group magbot >/dev/null || groupadd --system magbot
  id magbot >/dev/null 2>&1 || useradd --system --gid magbot --home-dir /var/lib/magbot --shell /usr/sbin/nologin magbot
  install -d -o magbot -g magbot -m 0750 /var/lib/magbot /var/lib/magbot/room
  install -d -m 0755 /etc/magbot /etc/mag-stack
  [[ -e /etc/magbot/config.json ]] || install -m 0644 "$REPO/ops/vm/config.json" /etc/magbot/config.json
  usermod -aG libvirt,kvm "$OWNER"
}

p_kernel() {
  install -m 0644 "$REPO/ops/boot/sysctl-mag.conf" /etc/sysctl.d/60-mag.conf
  sysctl --system >/dev/null
}

# Every secret has its place; this phase checks they are there and creates
# what can be generated. Nothing secret is printed.
p_secrets() {
  local missing=()
  if [[ ! -e /etc/default/mag-room ]]; then
    local pem pub priv
    pem=$(openssl ecparam -name prime256v1 -genkey -noout)
    priv=$(openssl ec -outform DER 2>/dev/null <<<"$pem" | tail -c +8 | head -c 32 | basenc --base64url | tr -d '=\n')
    pub=$(openssl ec -pubout -outform DER 2>/dev/null <<<"$pem" | tail -c 65 | basenc --base64url | tr -d '=\n')
    install -m 0600 /dev/null /etc/default/mag-room
    sed -e "s|^MAG_ROOM_VAPID_PUBLIC=.*|MAG_ROOM_VAPID_PUBLIC=$pub|" -e "s|^MAG_ROOM_VAPID_PRIVATE=.*|MAG_ROOM_VAPID_PRIVATE=$priv|" \
      "$TPL/mag-room.env" > /etc/default/mag-room
    note 'wrote /etc/default/mag-room with a new VAPID key pair (web push)'
  fi
  grep -q '^MAG_ROOM_BRAVE_KEY=.\+' /etc/default/mag-room || note 'optional: MAG_ROOM_BRAVE_KEY in /etc/default/mag-room (web search for the bots)'
  if [[ ! -s /var/lib/magbot/room/access.json ]]; then
    missing+=("/var/lib/magbot/room/access.json: copy $TPL/access.json and put the Tailscale login(s) of the owner in \"owners\" (chown magbot:magbot, 0640)")
  elif jq -e '.owners | index("chris@github")' /var/lib/magbot/room/access.json >/dev/null && [[ ${MAG_OWNER_LOGIN_OK:-0} != 1 ]]; then
    note 'access.json still names the template owner chris@github; set MAG_OWNER_LOGIN_OK=1 if that is really the login'
  fi
  [[ -s /etc/mag-acme/acme-dns.json ]] || missing+=("/etc/mag-acme/acme-dns.json (root 0600): the acme-dns account of mag.stardenburg.de and bot.stardenburg.de, see $TPL/acme-dns.json and docs/INSTALL.md (register once: curl -X POST https://auth.acme-dns.io/register, then CNAME _acme-challenge.<name> to its fulldomain)")
  [[ -s /etc/mag-stack/hf-token ]] || note 'optional: /etc/mag-stack/hf-token (root 0600, a Hugging Face token with access to speridlabs/idol): without it photo to 3D stays off'
  if ! tailscale status >/dev/null 2>&1 && [[ ! -s /etc/credstore/tailscale.authkey ]]; then
    note 'Tailscale: /etc/credstore/tailscale.authkey (root 0600) joins without asking; without it the next phase prints a login link'
  fi
  if (( ${#missing[@]} )); then stop "secrets missing:$(printf '\n  - %s' "${missing[@]}")"; fi
  install -d -m 0755 /etc/mag-acme
  [[ -e /etc/mag-acme/lego.env ]] || install -m 0644 "$REPO/ops/private-access/env.conf" /etc/mag-acme/lego.env
  chmod 0600 /etc/mag-acme/acme-dns.json
  chown magbot:magbot /var/lib/magbot/room/access.json
}

p_tailscale() {
  bash "$REPO/ops/tailscale/install.sh"
  tailscale status >/dev/null 2>&1 || stop 'Tailscale is not logged in yet: open the link above (or sudo tailscale up), then run again'
  local ip; ip=$(tailscale ip -4 | head -1)
  if [[ $ip != 100.123.114.16 ]]; then
    stop "this node has the Tailnet address $ip; MAG answers on 100.123.114.16 (nginx, DNS of mag.stardenburg.de).
  In the Tailscale admin console: remove the old node 'mag', name this one 'mag' and set its IPv4 to 100.123.114.16 (Machines > ... > Edit IP), then run again"
  fi
}

p_trust() {
  bash "$REPO/ops/trust/install-trust.sh"
  [[ -x /usr/local/bin/restic ]] || bash "$REPO/ops/backup/install-restic-0181.sh"
  [[ -x /usr/local/bin/lego ]] || bash "$REPO/ops/private-access/install-lego.sh"
}

p_build() {
  chown -R "$OWNER:$OWNER" "$REPO"
  [[ -f $REPO/build/build.ninja ]] || as_owner cmake -S "$REPO" -B "$REPO/build" -G Ninja -DCMAKE_BUILD_TYPE=Release
  as_owner ninja -C "$REPO/build" -j12
}

p_audit() {
  bash "$REPO/ops/audit/install-audit.sh" --apply
  bash "$REPO/ops/audit/install-tailscale-egress.sh"
}

p_libvirt() {
  systemctl enable --now libvirtd
  virsh net-info default >/dev/null 2>&1 || virsh net-define /usr/share/libvirt/networks/default.xml
  virsh net-autostart default
  virsh net-info default | grep -q 'Active:.*yes' || virsh net-start default
}

p_golden() {
  [[ -s /var/lib/libvirt/images/magbot/base/golden.qcow2 ]] && { note 'golden image present'; return; }
  pinned bash "$REPO/ops/vm/build-golden.sh"
}

# nginx's own configuration, the room's certificate and the bots' wildcard.
p_tls() {
  install -m 0644 "$REPO/ops/nginx/nginx.conf" /etc/nginx/nginx.conf
  install -D -m 0644 "$REPO/ops/nginx/nginx-restart.conf" /etc/systemd/system/nginx.service.d/mag-restart.conf
  rm -f /etc/nginx/sites-enabled/default
  systemctl daemon-reload
  nginx -t && systemctl enable --now nginx && systemctl reload nginx
  install -d -m 0755 /usr/local/lib/mag-acme
  install -m 0755 "$REPO/ops/private-access/issue-cert.sh" "$REPO/ops/private-access/deploy-hook.sh" /usr/local/lib/mag-acme/
  install -m 0644 "$REPO/ops/private-access/mag-acme-renew.service" "$REPO/ops/private-access/mag-acme-renew.timer" /etc/systemd/system/
  systemctl daemon-reload
  systemctl enable --now mag-acme-renew.timer
  [[ -s /etc/mag-nginx/tls/cert.pem ]] || /usr/local/lib/mag-acme/issue-cert.sh production
  [[ -s /etc/mag-bots/tls/current/cert.pem ]] || bash "$REPO/ops/bots/issue-wildcard.sh" run
}

p_gitea() {
  bash "$REPO/ops/gitea/install.sh"
  systemctl enable --now gitea.service
  bash "$REPO/ops/runners/install.sh"
}

# The model runtime (llama.cpp, pinned) and the speech recognizer, voices,
# Blender: built or fetched once into the owner's cache.
p_runtime() {
  as_owner bash "$REPO/ops/models/build-runtime.sh"
}
p_media() {
  as_owner bash "$REPO/ops/stt/bootstrap.sh"
  as_owner bash "$REPO/ops/tts/bootstrap.sh"
  as_owner bash "$REPO/ops/scene/bootstrap.sh"
  as_owner bash "$REPO/ops/scene/face/bootstrap.sh"
}

p_idol() {
  grep -qs 0x66a1 /sys/bus/pci/devices/*/device || { note 'no MI50: Idol skipped'; return; }
  MAG_USER=$OWNER pinned bash "$REPO/ops/idol/env/setup.sh"
}

# The release: room, gateway, model supervisor (it downloads and checks the
# models now), speech, voices, scenes, Idol, the bots' sites, timers, boot.
p_deploy() {
  MAG_DEPLOY_FIRST_INSTALL=1 bash "$REPO/ops/deploy.sh"
}

# Lena and Noah get their VMs (agent1, agent2) from the golden image.
p_bots() {
  local state=/var/lib/magbot/room/bots.json
  for _ in $(seq 1 30); do [[ -s $state ]] && break; sleep 2; done
  [[ -s $state ]] || stop 'the room has not written bots.json yet (journalctl -u mag-120)'
  local n=1 bot vm
  for bot in lena noah; do
    vm=$(jq -r --arg b "$bot" '.[] | select(.id==$b) | .vm // ""' "$state")
    if [[ -z $vm ]]; then
      vm=agent$n
      /usr/local/sbin/magbot-agent-ctl verify "$vm" >/dev/null 2>&1 || /usr/local/sbin/magbot-agent-ctl spawn "$vm"
      systemctl stop mag-120
      jq --arg b "$bot" --arg vm "$vm" 'map(if .id==$b then .vm=$vm else . end)' "$state" > "$state.new"
      chown magbot:magbot "$state.new"; mv "$state.new" "$state"
      systemctl start mag-120
    fi
    n=$((n + 1))
  done
}

p_funnel() {
  bash "$REPO/ops/tailscale/apply-gitea-funnel.sh" || note 'Gitea Funnel not applied (Funnel must be allowed for this node in the tailnet policy)'
}

p_backup() {
  bash "$REPO/ops/backup/install.sh"
}

p_check() {
  bash "$REPO/ops/health.sh" || note 'health check reports a problem (above)'
  bash "$REPO/ops/boot/ensure-boot.sh"
  cat <<EOF

MAG is installed. https://mag.stardenburg.de from a device of the tailnet.
 - The models download now (about 400 GB): /usr/local/lib/mag-models/mag-modeld status
 - Bot VMs: agent1 (Lena), agent2 (Noah); each bot's sheet shows its VM.
 - Backups: nightly room/Gitea, nightly VM saves, weekly whole system
   (connect a disk at /mnt/mag-backup or set MAG_SYSTEM_REPO, docs/OPERATIONS.md).
EOF
}

# ------------------------------------------------------------------ driver
mode=all; target=
case ${1:-} in
  --list) for p in "${PHASES[@]}"; do printf '%-10s %s\n' "$p" "$([[ -e $DONE/$p ]] && echo done || echo -)"; done; exit 0 ;;
  --only|--from|--again) mode=${1#--}; target=${2:-}; [[ " ${PHASES[*]} " == *" $target "* ]] || { echo "unknown phase '$target' (--list)" >&2; exit 2; } ;;
  '') ;;
  *) echo 'Usage: sudo ./install.sh [--list | --only PHASE | --from PHASE]' >&2; exit 2 ;;
esac
[[ $mode == again ]] && mode=only
started=0
for phase in "${PHASES[@]}"; do
  case $mode in
    only) [[ $phase == "$target" ]] || continue ;;
    from) [[ $phase == "$target" ]] && started=1; (( started )) || continue ;;
    all) [[ -e $DONE/$phase ]] && continue ;;
  esac
  log "$phase"
  "p_$phase"
  touch "$DONE/$phase"
done
log 'done'
