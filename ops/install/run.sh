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
#   sudo ./install.sh --probe         a trial install on a throwaway server:
#        everything that runs without a tailnet, a domain, KVM or a GPU
#        (no Tailscale, TLS, golden image, bot VMs, Idol, Funnel, backups),
#        no model downloads, a placeholder owner; the room answers on
#        127.0.0.1:8766. Any 2 vCPU / 4 GB Ubuntu 24.04 machine will do.
#
# Secrets are never in the repository. Where one is needed the phase stops
# with what to put where; the templates are in ops/install/templates and
# docs/INSTALL.md lists them all. Builds and downloads run on CPUs 28-55.
set -euo pipefail
[[ $EUID == 0 ]] || { echo 'Run with sudo: sudo ./install.sh' >&2; exit 1; }
REPO=$(cd -- "$(dirname -- "$(readlink -f "$0")")/../.." && pwd)
DONE=/var/lib/mag-install
TPL=$REPO/ops/install/templates
# Who and where this host is: /etc/mag-stack/host.env, written once from the
# template with the account that ran sudo as its owner (ops/host.env).
install -d -m 0755 /etc/mag-stack
if [[ ! -e /etc/mag-stack/host.env ]]; then
  sed "s/^MAG_OWNER=.*/MAG_OWNER=${MAG_OWNER:-${SUDO_USER:-chris}}/" "$TPL/host.env" > /etc/mag-stack/host.env
  chmod 0644 /etc/mag-stack/host.env
fi
# shellcheck source=../host.sh
source "$REPO/ops/host.sh"
OWNER=$MAG_OWNER OWNER_HOME=$MAG_OWNER_HOME
CPUS=${MAG_BUILD_CPUS:-28-55}
# A smaller machine (a trial install) has no CPUs 28-55: then all of them.
taskset -c "$CPUS" true 2>/dev/null || CPUS=0-$(( $(nproc --all) - 1 ))
JOBS=$(( $(nproc) < 12 ? $(nproc) : 12 ))
PROBE=0
[[ " $* " == *" --probe "* ]] && PROBE=1
install -d -m 0700 "$DONE"
log() { printf '\n== install: %s\n' "$*"; }
note() { printf 'install: %s\n' "$*"; }
stop() { printf '\ninstall: STOPPED in phase %s:\n  %s\nFix that, then run sudo ./install.sh again; it goes on here.\n' "$phase" "$*" >&2; exit 3; }
as_owner() { sudo -u "$OWNER" -H taskset -c "$CPUS" nice -n 10 "$@"; }
pinned() { taskset -c "$CPUS" nice -n 10 "$@"; }

PHASES=(preflight packages users kernel secrets tailscale trust build libvirt audit golden tls gitea runtime media idol deploy bots funnel backup check)
# What a trial install leaves out (--probe): what needs a tailnet, a domain, KVM or the MI50.
PROBE_SKIP=" tailscale golden tls idol bots funnel backup "

# ------------------------------------------------------------------ phases
p_preflight() {
  . /etc/os-release
  [[ ${ID:-} == ubuntu && ${VERSION_ID:-} == 24.04 ]] || stop "Ubuntu 24.04 is required (this is ${PRETTY_NAME:-unknown})"
  [[ $(uname -m) == x86_64 ]] || stop 'x86_64 is required'
  id "$OWNER" >/dev/null 2>&1 && [[ -d $OWNER_HOME ]] || stop "the owner account '$OWNER' (MAG_OWNER in /etc/mag-stack/host.env) does not exist"
  note "owner $OWNER ($OWNER_HOME), room https://$MAG_DOMAIN, bots *.$MAG_BOTS_ZONE (/etc/mag-stack/host.env)"
  [[ $REPO == "$OWNER_HOME/mag-stack" ]] || note "WARN: the checkout is $REPO; deploys expect $OWNER_HOME/mag-stack"
  [[ -e /dev/kvm ]] || (( PROBE )) || stop '/dev/kvm is missing: enable virtualisation in the BIOS'
  local nodes cpus
  nodes=$(ls -d /sys/devices/system/node/node[0-9]* 2>/dev/null | wc -l)
  cpus=$(nproc --all)
  (( nodes >= 2 && cpus >= 56 )) || note "WARN: $nodes NUMA nodes, $cpus CPUs; MAG is laid out for 2 nodes and 56 CPUs (0-13 Lena, 14-27 Noah, 28-55 the rest; ops/models/models.json)"
  local free; free=$(df -BG --output=avail / | tail -1 | tr -dc 0-9)
  (( free >= 700 || PROBE )) || note "WARN: ${free} GB free; the models and weights need about 600 GB"
  if grep -qs 0x66a1 /sys/bus/pci/devices/*/device; then note 'MI50 found (Idol: photo to 3D, pictures, video; power cap 82 W)'
  else note 'no MI50: the Idol worker stays off; everything else works'; fi
}

p_packages() {
  local list
  list=$(grep -v '^#' "$REPO/ops/install/packages.txt" | tr '\n' ' ')
  apt-get update
  # shellcheck disable=SC2086
  DEBIAN_FRONTEND=noninteractive apt-get install -y $list
  # Go for the browser gateway (mag-room/webrtc), at a fixed minor release.
  snap list go >/dev/null 2>&1 || snap install go --classic --channel=1.27/stable
}

p_users() {
  getent group magbot >/dev/null || groupadd --system magbot
  id magbot >/dev/null 2>&1 || useradd --system --gid magbot --home-dir /var/lib/magbot --shell /usr/sbin/nologin magbot
  install -d -o magbot -g magbot -m 0750 /var/lib/magbot /var/lib/magbot/room
  install -d -m 0755 /etc/magbot /etc/mag-stack
  [[ -e /etc/magbot/config.json ]] || install -m 0644 "$REPO/ops/vm/config.json" /etc/magbot/config.json
  usermod -aG libvirt,kvm "$OWNER"
  # A trial machine has no CPUs 28-55 and one NUMA node: the services run on
  # whatever it has (persistent control drop-ins; deploys leave them alone).
  if (( PROBE )) && ! taskset -c 28-55 true 2>/dev/null; then
    local u
    for u in mag-120.service mag-modeld.service mag-scene.service mag-stt.service mag-gitea-runner.service mag-idol.service \
             mag-model@.service mag-model-fetch@.service; do
      install -d "/etc/systemd/system.control/$u.d"
      printf '[Service]\nCPUAffinity=\nAllowedCPUs=\nNUMAPolicy=default\n' > "/etc/systemd/system.control/$u.d/90-trial-cpus.conf"
    done
    systemctl daemon-reload
    note 'trial machine: services may use every CPU (/etc/systemd/system.control/*/90-trial-cpus.conf)'
  fi
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
  if (( PROBE )) && [[ ! -s /var/lib/magbot/room/access.json ]]; then
    # A trial install has no owner on a tailnet: a placeholder nobody logs in with.
    jq '.owners = ["probe@example.invalid"]' "$TPL/access.json" > /var/lib/magbot/room/access.json
    chown magbot:magbot /var/lib/magbot/room/access.json; chmod 0640 /var/lib/magbot/room/access.json
  fi
  if [[ ! -s /var/lib/magbot/room/access.json ]]; then
    missing+=("/var/lib/magbot/room/access.json: copy $TPL/access.json and put the Tailscale login(s) of the owner in \"owners\" (chown magbot:magbot, 0640)")
  elif jq -e '.owners | index("chris@github")' /var/lib/magbot/room/access.json >/dev/null && [[ ${MAG_OWNER_LOGIN_OK:-0} != 1 ]]; then
    note 'access.json still names the template owner chris@github; set MAG_OWNER_LOGIN_OK=1 if that is really the login'
  fi
  [[ -s /etc/mag-acme/acme-dns.json ]] || (( PROBE )) || missing+=("/etc/mag-acme/acme-dns.json (root 0600): the acme-dns account of $MAG_DOMAIN and $MAG_BOTS_ZONE, see $TPL/acme-dns.json and docs/INSTALL.md (register once: curl -X POST https://auth.acme-dns.io/register, then CNAME _acme-challenge.<name> to its fulldomain)")
  [[ -s /etc/mag-stack/hf-token ]] || note 'optional: /etc/mag-stack/hf-token (root 0600, a Hugging Face token with access to speridlabs/idol): without it photo to 3D stays off'
  if ! tailscale status >/dev/null 2>&1 && [[ ! -s /etc/credstore/tailscale.authkey ]]; then
    note 'Tailscale: /etc/credstore/tailscale.authkey (root 0600) joins without asking; without it the next phase prints a login link'
  fi
  if (( ${#missing[@]} )); then stop "secrets missing:$(printf '\n  - %s' "${missing[@]}")"; fi
  install -d -m 0755 /etc/mag-acme
  [[ -e /etc/mag-acme/lego.env ]] || { mag_render "$REPO/ops/private-access/env.conf" > /etc/mag-acme/lego.env; chmod 0644 /etc/mag-acme/lego.env; }
  [[ ! -e /etc/mag-acme/acme-dns.json ]] || chmod 0600 /etc/mag-acme/acme-dns.json
  # A trial install downloads no model (mag-modeld leaves them missing).
  if (( PROBE )); then touch /etc/mag-stack/no-model-download; fi
  chown magbot:magbot /var/lib/magbot/room/access.json
}

p_tailscale() {
  bash "$REPO/ops/tailscale/install.sh"
  tailscale status >/dev/null 2>&1 || stop 'Tailscale is not logged in yet: open the link above (or sudo tailscale up), then run again'
  local ip want
  ip=$(tailscale ip -4 | head -1)
  want=$(grep -h '^MAG_TAILNET_IP=' /etc/mag-stack/host.env | tail -1 | cut -d= -f2)
  # A fixed address (the old host's): with a Tailscale API key the node takes
  # it by itself; an offline node of the same name that holds it goes first.
  if [[ -n $want && $ip != "$want" ]]; then
    [[ -s /etc/credstore/tailscale-api.key ]] ||
      stop "this node has the address $ip, /etc/mag-stack/host.env wants $want. Either put a Tailscale API key into
  /etc/credstore/tailscale-api.key (root 0600; the installer then moves the address), or set MAG_TAILNET_IP= (empty) to use $ip
  and point the DNS of $MAG_DOMAIN and *.$MAG_BOTS_ZONE at it"
    tailnet_take_address "$want"
    ip=$(tailscale ip -4 | head -1)
    [[ $ip == "$want" ]] || stop "the address $want was asked for; this node still has $ip (sudo tailscale down && sudo tailscale up, then run again)"
  fi
  MAG_TAILNET_IP=$ip
  local dns; dns=$(dig +short "$MAG_DOMAIN" A 2>/dev/null | tail -1)
  [[ $dns == "$ip" ]] || note "WARN: $MAG_DOMAIN resolves to ${dns:-nothing}, this node is $ip: point its A record (and *.$MAG_BOTS_ZONE) at $ip"
}
# Moves the Tailnet address <ip> to this node through the Tailscale API.
tailnet_take_address() {
  local want=$1 key self api=https://api.tailscale.com/api/v2 devices old
  key=$(cat /etc/credstore/tailscale-api.key)
  self=$(tailscale status --json | jq -r '.Self.ID')
  devices=$(curl -fsS -u "$key:" "$api/tailnet/-/devices") || stop 'Tailscale API refused the key (devices:read/write needed)'
  # Only a node that is offline and is not this one gives its address up.
  old=$(jq -r --arg ip "$want" --arg self "$self" '.devices[] | select((.addresses | index($ip)) and .nodeId != $self and .id != $self) |
        select((.lastSeen // "") < (now - 600 | todate)) | .id' <<<"$devices" | head -1)
  if [[ -n $old ]]; then
    note "removing the offline node that held $want"
    curl -fsS -u "$key:" -X DELETE "$api/device/$old" >/dev/null
  fi
  curl -fsS -u "$key:" -H 'Content-Type: application/json' -d "{\"ipv4\":\"$want\"}" "$api/device/$self/ip" >/dev/null ||
    stop "Tailscale API did not give this node $want (still held by an online node?)"
  # The node learns its new address with the next network map.
  for _ in $(seq 1 30); do [[ $(tailscale ip -4 | head -1) == "$want" ]] && return 0; sleep 2; done
}

p_trust() {
  bash "$REPO/ops/trust/install-trust.sh"
  [[ -x /usr/local/bin/restic ]] || bash "$REPO/ops/backup/install-restic-0181.sh"
  [[ -x /usr/local/bin/lego ]] || bash "$REPO/ops/private-access/install-lego.sh"
}

p_build() {
  chown -R "$OWNER:$OWNER" "$REPO"
  [[ -f $REPO/build/build.ninja ]] || as_owner cmake -S "$REPO" -B "$REPO/build" -G Ninja -DCMAKE_BUILD_TYPE=Release
  as_owner ninja -C "$REPO/build" -j"$JOBS"
}

p_audit() {
  bash "$REPO/ops/audit/install-audit.sh" --apply
  bash "$REPO/ops/audit/install-tailscale-egress.sh"
}

p_libvirt() {
  systemctl enable --now libvirtd
  virsh net-info default >/dev/null 2>&1 || virsh net-define /usr/share/libvirt/networks/default.xml
  virsh net-autostart default
  # (no grep -q in a pipe: under pipefail its early exit fails the pipe)
  [[ $(virsh net-info default) =~ Active:[[:space:]]+yes ]] || virsh net-start default
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
  local u
  for u in mag-acme-renew.service mag-acme-renew.timer; do mag_render "$REPO/ops/private-access/$u" > "/etc/systemd/system/$u"; done
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
  pinned bash "$REPO/ops/idol/env/setup.sh"
}

# The release: room, gateway, model supervisor (it downloads and checks the
# models now), speech, voices, scenes, Idol, the bots' sites, timers, boot.
p_deploy() {
  MAG_DEPLOY_NO_INGRESS=$PROBE MAG_BUILD_JOBS=$JOBS MAG_BUILD_CPUS=$CPUS bash "$REPO/ops/deploy.sh"
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
  bash "$REPO/ops/boot/ensure-boot.sh"
  if (( PROBE )); then
    # What a trial install must show: every service of the release up and answering.
    local bad=0 u
    for u in mag-120 mag-audit-gateway mag-audit-webproxy mag-modeld mag-scene mag-stt mag-browser-webrtc gitea mag-gitea-runner nginx; do
      if systemctl is-active --quiet "$u"; then note "ok      $u"; else note "FAILED  $u"; bad=1; fi
    done
    for url in http://127.0.0.1:8766/readyz http://127.0.0.1:8080/audit/v1/health http://127.0.0.1:8795/api/scene/health http://127.0.0.1:3000/api/healthz; do
      if curl --noproxy '*' -fsS --max-time 10 -o /dev/null "$url"; then note "ok      $url"; else note "FAILED  $url"; bad=1; fi
    done
    (( bad == 0 )) || stop 'a service of the trial install is not up (journalctl -u <unit>)'
    note 'PROBE OK: the release installs and runs from the repository alone'
    return
  fi
  bash "$REPO/ops/health.sh" || note 'health check reports a problem (above)'
  cat <<EOF

MAG is installed. https://$MAG_DOMAIN from a device of the tailnet.
 - The models download now (about 400 GB): /usr/local/lib/mag-models/mag-modeld status
 - Bot VMs: agent1 (Lena), agent2 (Noah); each bot's sheet shows its VM.
 - Backups: nightly room/Gitea, nightly VM saves, weekly whole system
   (connect a disk at /mnt/mag-backup or set MAG_SYSTEM_REPO, docs/OPERATIONS.md).
EOF
}

# ------------------------------------------------------------------ driver
mode=all; target=
args=()
for a in "$@"; do [[ $a == --probe ]] || args+=("$a"); done
set -- "${args[@]}"
case ${1:-} in
  --list) for p in "${PHASES[@]}"; do printf '%-10s %s\n' "$p" "$([[ -e $DONE/$p ]] && echo done || echo -)"; done; exit 0 ;;
  --only|--from|--again) mode=${1#--}; target=${2:-}; [[ " ${PHASES[*]} " == *" $target "* ]] || { echo "unknown phase '$target' (--list)" >&2; exit 2; } ;;
  '') ;;
  *) echo 'Usage: sudo ./install.sh [--probe] [--list | --only PHASE | --from PHASE]' >&2; exit 2 ;;
esac
[[ $mode == again ]] && mode=only
started=0
for phase in "${PHASES[@]}"; do
  case $mode in
    only) [[ $phase == "$target" ]] || continue ;;
    from) [[ $phase == "$target" ]] && started=1; (( started )) || continue ;;
    all) [[ -e $DONE/$phase ]] && continue ;;
  esac
  if (( PROBE )) && [[ $PROBE_SKIP == *" $phase "* ]]; then note "skipped (trial install): $phase"; continue; fi
  log "$phase"
  "p_$phase"
  touch "$DONE/$phase"
done
log 'done'
