#!/usr/bin/env bash
# Blank Ubuntu 24.04 host -> running MAG stack. Guide: docs/INSTALL.md.
# Does not enable console lockdown. Does not restart a resident model. DeepSeek runs once per NUMA node.
#   bash ops/bootstrap.sh --dry-run
#   sudo bash ops/bootstrap.sh --archive /var/backups/mag-full/<id> [--download-models] [--start-llm]
#   sudo bash ops/bootstrap.sh --owner LOGIN [--download-models]     # no archive: fresh state
# Configuration comes from the repo (units, nginx vhosts, ACME units, env
# defaults are installed by the installers below). The archive provides only
# state and secrets (room, Gitea, Pages, ACME account + certs, Tailscale node,
# credstore, audit CA). Secrets an operator provides without an archive are
# read from files in /etc/credstore (see docs/INSTALL.md); none are printed.
set -euo pipefail
ROOT=$(cd -- "$(dirname -- "$(readlink -f "$0")")/.." && pwd)
# shellcheck source=build-targets.sh
source "$ROOT/ops/build-targets.sh"
DRY=0
ARCHIVE=
OWNER=
DOWNLOAD=0
START_LLM=0
DEPLOY=1
GOLDEN=1
LLM_BUILD=1
HOST_CONFIG=0
STUBS=0
usage() {
  cat <<EOF
Usage: $0 --dry-run
       $0 [--archive DIR] [--owner LOGIN] [--download-models] [--start-llm]
          [--no-deploy] [--no-golden] [--no-llm-build] [--restore-host-config]
          [--test-stubs]
  --archive DIR          verified /var/backups/mag-full/<id> (state + secrets)
  --owner LOGIN          Tailscale login of the room owner (fresh host without archive)
  --download-models      fetch and verify the pinned DeepSeek Q4 GGUF shards
  --start-llm            start DeepSeek per-NUMA units if inactive
  --no-golden            skip the bot VM base image build
  --no-llm-build         skip llama.cpp / ik_llama / Qwen runtime builds
  --restore-host-config  also copy etc/nginx, etc/systemd/system, etc/cron.d
                         from the archive (legacy; repo templates are default)
  --test-stubs           test hosts only (/etc/mag-stack/TEST-HOST): stub LLM,
                         stub whisper model, no golden image, no models
EOF
}
while (( $# )); do
  case "$1" in
    --dry-run) DRY=1; shift ;;
    --archive) ARCHIVE=${2:-}; shift 2 ;;
    --owner) OWNER=${2:-}; shift 2 ;;
    --download-models) DOWNLOAD=1; shift ;;
    --start-llm) START_LLM=1; shift ;;
    --no-deploy) DEPLOY=0; shift ;;
    --no-golden) GOLDEN=0; shift ;;
    --no-llm-build) LLM_BUILD=0; shift ;;
    --restore-host-config) HOST_CONFIG=1; shift ;;
    --test-stubs) STUBS=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument $1" >&2; usage >&2; exit 2 ;;
  esac
done

phase() { printf 'PHASE %s\n' "$1"; }
run() {
  printf '+'
  printf ' %q' "$@"
  printf '\n'
  if (( DRY )); then return 0; fi
  "$@"
}
note() { printf '%s\n' "$*"; }
as_chris() { runuser -u chris -- env HOME=/home/chris PATH=/usr/local/bin:/usr/bin:/bin "$@"; }

if [[ -n $OWNER && ! $OWNER =~ ^[A-Za-z0-9._@+-]{1,160}$ ]]; then
  echo 'Invalid --owner login' >&2; exit 2
fi
if (( STUBS )) && (( DRY == 0 )) && [[ ! -e /etc/mag-stack/TEST-HOST ]]; then
  echo '--test-stubs needs /etc/mag-stack/TEST-HOST (never on a production host)' >&2; exit 1
fi
if (( DRY == 0 )); then
  [[ $EUID == 0 ]] || { echo 'Root required (or pass --dry-run)' >&2; exit 1; }
  if [[ -n "$ARCHIVE" ]]; then
    [[ -f "$ARCHIVE/VERIFIED" && -d "$ARCHIVE/repo" && ! -L "$ARCHIVE" ]] || {
      echo 'Pass --archive DIR for one verified /var/backups/mag-full directory' >&2; exit 1;
    }
  fi
  if systemctl is-active --quiet mag-120.service; then
    echo 'mag-120 is already active. Use ops/deploy.sh. Bootstrap is for a blank host.' >&2
    exit 1
  fi
fi
STAGE=${MAG_BOOTSTRAP_STAGE:-/var/tmp/mag-bootstrap-restore}

phase release-check
if (( DRY )); then
  echo '+ require Ubuntu 24.04 (noble) unless MAG_ALLOW_OTHER_RELEASE=1'
else
  # shellcheck disable=SC1091
  . /etc/os-release
  if [[ ${ID:-} != ubuntu || ${VERSION_CODENAME:-} != noble ]]; then
    [[ ${MAG_ALLOW_OTHER_RELEASE:-0} == 1 ]] ||
      { echo "MAG targets Ubuntu 24.04 (noble); this is ${PRETTY_NAME:-unknown}. MAG_ALLOW_OTHER_RELEASE=1 overrides." >&2; exit 1; }
    echo "WARN: ${PRETTY_NAME:-unknown} is not the tested release" >&2
  fi
fi

phase users
# Before packages: libvirt, dnsmasq & co. take free system uids counting down
# from 999, so on a blank host uid 993 would be gone before magbot exists.
if (( DRY )); then
  echo '+ id magbot || useradd --system --uid 993 --user-group --home-dir /var/lib/magbot --create-home magbot'
  echo '+ id chris || useradd --create-home --shell /bin/bash chris'
else
  if ! id magbot >/dev/null 2>&1; then
    if getent passwd 993 >/dev/null; then
      echo 'uid 993 is taken by someone other than magbot' >&2; exit 1
    fi
    useradd --system --uid 993 --user-group --home-dir /var/lib/magbot --create-home --shell /usr/sbin/nologin magbot
  fi
  id chris >/dev/null 2>&1 || useradd --create-home --shell /bin/bash chris
fi
if (( DRY == 0 )) && ! runuser -u chris -- test -r "$ROOT/ops/bootstrap.sh"; then
  echo "chris cannot read $ROOT. Clone the repo as chris (e.g. /home/chris/mag-stack) and rerun." >&2
  exit 1
fi

phase packages
run apt-get update
run env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
  build-essential cmake ninja-build g++ pkg-config file patch xz-utils \
  libssl-dev libcurl4-openssl-dev libarchive-dev \
  nginx jq sqlite3 numactl git curl ca-certificates openssl python3 python3-venv \
  bzip2 dnsutils acl golang-go nodejs nftables iproute2 openssh-client sudo dnsmasq-base \
  libvirt-daemon-system libvirt-clients qemu-system-x86 qemu-utils virtinst cloud-image-utils
# Do not rely on package postinst autostart (policy-rc.d, image defaults):
# deploy needs nginx up, bot VMs need libvirtd.
run systemctl enable --now nginx.service libvirtd.service

phase restic
run bash "$ROOT/ops/backup/install-restic-0181.sh"

phase restore
if [[ -z "$ARCHIVE" ]] && (( DRY == 0 )); then
  echo 'PHASE restore-skipped (no --archive: fresh state)'
elif (( DRY )); then
  echo "+ bash $ROOT/ops/backup/restore.sh --no-lock --snapshot <archive> --part all --target $STAGE"
else
  rm -rf -- "$STAGE"
  install -d -m 0700 "$STAGE"
  name=$(basename "$ARCHIVE")
  RESTIC_REPOSITORY="$ARCHIVE/repo" bash "$ROOT/ops/backup/restore.sh" --no-lock \
    --snapshot "$name" --part all --target "$STAGE"
fi

place() {
  local rel=$1 from dest
  from="$STAGE/$rel"
  dest="/$rel"
  if (( DRY )); then
    echo "+ cp -a $from $dest   # only when the destination is absent"
    return 0
  fi
  [[ -e "$from" || -L "$from" ]] || return 0
  if [[ -d "$dest" && ! -L "$dest" && -d "$from" && ! -L "$from" ]]; then
    # Merge one level: files an operator placed beforehand (credstore keys,
    # /etc/mag-stack/TEST-HOST, ...) must not hide the rest of the archive.
    local entry
    for entry in "$from"/* "$from"/.[!.]*; do
      [[ -e "$entry" || -L "$entry" ]] || continue
      place "$rel/$(basename "$entry")"
    done
    return 0
  fi
  if [[ -e "$dest" && "${MAG_BOOTSTRAP_REPLACE:-0}" != 1 ]]; then
    echo "Leave existing $dest"
    return 0
  fi
  install -d -m 0755 "$(dirname "$dest")"
  rm -rf -- "$dest"
  cp -a -- "$from" "$dest"
}

phase place-state
# State and secrets only. Units, nginx vhosts and ACME units come from the
# repo (deploy.sh and the installers below); repo state wins for templates.
STATE_PATHS=(var/lib/magbot/room var/lib/gitea etc/gitea var/lib/mag-pages etc/mag-pages
  etc/mag-acme etc/mag-nginx etc/magbot etc/default/mag-room etc/mag-stack
  etc/credstore etc/mag-audit var/lib/mag-audit home/chris/llama-templates
  var/spool/cron/crontabs)
HOST_CONFIG_PATHS=(etc/nginx etc/systemd/system etc/cron.d)
if [[ -z "$ARCHIVE" ]] && (( DRY == 0 )); then
  echo 'PHASE place-state-skipped (no archive)'
else
  for rel in "${STATE_PATHS[@]}"; do place "$rel"; done
  if (( HOST_CONFIG )); then
    for rel in "${HOST_CONFIG_PATHS[@]}"; do place "$rel"; done
  else
    note "Not restored (repo templates instead): ${HOST_CONFIG_PATHS[*]} (pass --restore-host-config for the old behaviour)"
  fi
fi

phase config
if (( DRY )); then
  echo '+ install ops/private-access/env.conf -> /etc/mag-acme/lego.env   # only when absent'
  echo '+ /etc/credstore/mag-acme-dns.json -> /etc/mag-acme/acme-dns.json   # only when absent'
  echo '+ write /var/lib/magbot/room/access.json from --owner   # only when absent'
  echo '+ install -d /etc/magbot   # VM limits default without config.json; secrets come from the archive'
  echo '+ room-model.env replaces legacy secondary-model.env (no resident model restart)'
else
  install -d -m 0755 /etc/mag-stack
  install -d -m 0700 /etc/mag-acme /etc/credstore
  # magbot-native uses built-in VM limits without /etc/magbot/config.json;
  # the backup treats the directory as essential host state.
  install -d -m 0750 /etc/magbot
  if [[ ! -e /etc/mag-acme/lego.env ]]; then
    install -m 0600 -o root -g root "$ROOT/ops/private-access/env.conf" /etc/mag-acme/lego.env
  fi
  if [[ ! -e /etc/mag-acme/acme-dns.json && -s /etc/credstore/mag-acme-dns.json ]]; then
    install -m 0600 -o root -g root /etc/credstore/mag-acme-dns.json /etc/mag-acme/acme-dns.json
    echo 'Installed acme-dns credentials from /etc/credstore (not printed)'
  fi
  install -d -o magbot -g magbot -m 0750 /var/lib/magbot/room
  if [[ ! -e /var/lib/magbot/room/access.json ]]; then
    if [[ -n $OWNER ]]; then
      jq -n --arg o "$OWNER" '{owners: [$o]}' > /var/lib/magbot/room/access.json.new
      chown magbot:magbot /var/lib/magbot/room/access.json.new
      chmod 0640 /var/lib/magbot/room/access.json.new
      mv -f /var/lib/magbot/room/access.json.new /var/lib/magbot/room/access.json
      echo "Room owner: $OWNER"
    else
      echo 'WARN: no access.json and no --owner; deploy will refuse the nginx ingress' >&2
    fi
  fi
fi

phase tailscale
if [[ -n "$ARCHIVE" ]] || (( DRY )); then
  run bash "$ROOT/ops/tailscale/install.sh" --state "$STAGE"
else
  run bash "$ROOT/ops/tailscale/install.sh"
fi

phase lego
run bash "$ROOT/ops/private-access/install-lego.sh"

phase gitea
run bash "$ROOT/ops/gitea/install.sh"

phase gitea-mirror
if (( DRY )); then
  echo "+ bash $ROOT/ops/gitea/mirror-github.sh   # only when both credstore token files exist"
elif [[ -s /etc/credstore/gitea-mirror.token && -s /etc/credstore/github-mirror.token ]]; then
  for _ in $(seq 1 30); do curl -fsS --max-time 2 -o /dev/null http://127.0.0.1:3000/api/healthz && break; sleep 1; done
  bash "$ROOT/ops/gitea/mirror-github.sh" || echo 'WARN: Gitea mirror not set up (bootstrap continues)' >&2
else
  echo 'PHASE gitea-mirror-skipped (no /etc/credstore/{gitea,github}-mirror.token)'
fi

phase build
if (( DRY )); then
  echo "+ runuser -u chris -- cmake -S $ROOT -B $ROOT/build -G Ninja -DCMAKE_BUILD_TYPE=Release"
  echo "+ runuser -u chris -- cmake --build $ROOT/build --target ${MAG_BUILD_TARGETS[*]}"
  echo '+ go build -mod=mod -o build/mag-browser-webrtc ./mag-room/webrtc'
else
  # As the checkout owner: a root-owned build/ breaks later update.sh runs.
  owner=$(stat -c %U "$ROOT")
  runuser -u "$owner" -- cmake -S "$ROOT" -B "$ROOT/build" -G Ninja -DCMAKE_BUILD_TYPE=Release
  runuser -u "$owner" -- cmake --build "$ROOT/build" -j "${MAG_BUILD_JOBS:-$(nproc)}" --target "${MAG_BUILD_TARGETS[@]}"
  if [[ ! -x "$ROOT/build/mag-browser-webrtc" ]]; then
    # shellcheck disable=SC2016
    runuser -u "$owner" -- env HOME="$(getent passwd "$owner" | cut -d: -f6)" \
      bash -c 'cd "$1/mag-room/webrtc" && go build -mod=mod -o "$1/build/mag-browser-webrtc" .' _ "$ROOT"
  fi
fi

phase voice
if (( DRY )); then
  echo "+ runuser -u chris -- bash $ROOT/ops/bootstrap-voice.sh   # whisper.cpp $(cut -d' ' -f1 "$ROOT/ops/whisper.lock"), model SHA256-pinned"
elif (( STUBS )); then
  as_chris env MAG_VOICE_MODEL_STUB=1 bash "$ROOT/ops/bootstrap-voice.sh"
else
  as_chris bash "$ROOT/ops/bootstrap-voice.sh"
fi

phase llm-runtime
if (( STUBS )); then
  if (( DRY )); then
    echo '+ install ops/tests/stub-llm.py as mag-llm-stub.service on 127.0.0.1:8090/8091 (test host only)'
  else
    install -d -m 0755 /usr/local/lib/mag-test
    install -m 0755 "$ROOT/ops/tests/stub-llm.py" /usr/local/lib/mag-test/stub-llm.py
    cat > /etc/systemd/system/mag-llm-stub.service <<'EOF'
[Unit]
Description=MAG TEST stub LLM (ops/tests/stub-llm.py), never on production
ConditionPathExists=/etc/mag-stack/TEST-HOST
[Service]
User=nobody
ExecStart=/usr/bin/python3 /usr/local/lib/mag-test/stub-llm.py 8090 8091
Restart=on-failure
[Install]
WantedBy=multi-user.target
EOF
    systemctl daemon-reload
    systemctl enable --now mag-llm-stub.service
  fi
elif (( LLM_BUILD )); then
  if (( DRY )); then
    echo "+ runuser -u chris -- bash $ROOT/ops/deepseek/build-ik.sh"
  else
    ik_bin=$(runuser -u chris -- env HOME=/home/chris PATH=/usr/local/bin:/usr/bin:/bin \
      bash "$ROOT/ops/deepseek/build-ik.sh" | tail -n 1)
    [[ -x $ik_bin ]] || { echo 'ik_llama build did not produce a binary' >&2; exit 1; }
  fi
else
  echo 'PHASE llm-runtime-skipped (--no-llm-build)'
fi

phase room-model
if (( ! STUBS )); then
  if (( DRY )); then bash "$ROOT/ops/room/install.sh" --dry-run
  else
    new_ds_env=0
    [[ -e /etc/mag-llm/ds-node.env ]] || new_ds_env=1
    bash "$ROOT/ops/room/install.sh"
    # The pinned ik source contains the matching DeepSeek Jinja template.
    ds_template=/home/chris/llama-templates/deepseek-ai-DeepSeek-V4-Flash-0731.jinja
    if [[ ! -s $ds_template && -s /home/chris/src/ik_llama.cpp/models/templates/deepseek-ai-DeepSeek-V4-Flash-0731.jinja ]]; then
      install -d -o chris -g chris /home/chris/llama-templates
      install -m 0644 -o chris -g chris /home/chris/src/ik_llama.cpp/models/templates/deepseek-ai-DeepSeek-V4-Flash-0731.jinja "$ds_template"
    fi
    if (( new_ds_env && LLM_BUILD )); then
      sed -i "s|^IK_BIN=.*|IK_BIN=$ik_bin|" /etc/mag-llm/ds-node.env
    fi
  fi
fi

phase golden
if (( STUBS || ! GOLDEN )); then
  echo 'PHASE golden-skipped (bot creation stays disabled until ops/vm/build-golden.sh has run)'
elif (( DRY )); then
  echo "+ bash $ROOT/ops/vm/build-golden.sh   # only when base/golden.qcow2 is absent"
else
  systemctl enable --now libvirtd.service
  virsh -c qemu:///system net-info default >/dev/null 2>&1 &&
    { virsh -c qemu:///system net-autostart default >/dev/null; virsh -c qemu:///system net-start default >/dev/null 2>&1 || true; }
  bash "$ROOT/ops/vm/build-golden.sh"
fi

phase backup-install
if (( STUBS )); then
  run env MAG_BACKUP_ALLOW_SINGLE_NODE=1 bash "$ROOT/ops/backup/install.sh"
else
  run bash "$ROOT/ops/backup/install.sh"
fi

phase audit
# Central web/LLM audit: native gateway binary, mitmproxy web proxy, CA, VM
# package, nftables. A fresh host has no bot domains yet, so the libvirt
# default network is passed explicitly.
if (( DRY )); then
  echo "+ VM_IFACES=virbr0 VM_SUBNET=192.168.122.0/24 VM_HOST_IP=192.168.122.1 bash $ROOT/ops/audit/install-audit.sh --apply"
else
  systemctl enable --now libvirtd.service
  virsh -c qemu:///system net-autostart default >/dev/null 2>&1 || true
  virsh -c qemu:///system net-start default >/dev/null 2>&1 || true
  env VM_IFACES="${VM_IFACES:-virbr0}" VM_SUBNET="${VM_SUBNET:-192.168.122.0/24}" \
    VM_HOST_IP="${VM_HOST_IP:-192.168.122.1}" VM_CLIENT_MAP="${VM_CLIENT_MAP:-}" \
    bash "$ROOT/ops/audit/install-audit.sh" --apply
fi

phase gateway
# install-audit.sh installed the binary and unit; this stays the idempotent
# backstop for hosts where the audit step was done earlier.
if (( DRY )); then
  echo '+ install build/mag-audit-gateway -> /opt/mag-audit/bin   # only when the gateway is not running'
else
  if ! systemctl is-active --quiet mag-audit-gateway.service; then
    install -d -m 0755 /opt/mag-audit/bin
    install -m 0755 "$ROOT/build/mag-audit-gateway" /opt/mag-audit/bin/.mag-audit-gateway.next
    mv -f /opt/mag-audit/bin/.mag-audit-gateway.next /opt/mag-audit/bin/mag-audit-gateway
  fi
fi
if [[ ! -e /etc/systemd/system/mag-audit-gateway.service ]] || (( DRY )); then
  run install -m 0644 "$ROOT/ops/audit/systemd/mag-audit-gateway.service" /etc/systemd/system/mag-audit-gateway.service
fi
run systemctl daemon-reload
run systemctl enable --now mag-audit-gateway.service

phase tailscale-egress
if (( DRY )); then
  echo '+ bash ops/audit/install-tailscale-egress.sh   # only when inet mag_audit output exists'
else
  if command -v nft >/dev/null && nft list chain inet mag_audit output >/dev/null 2>&1; then
    bash "$ROOT/ops/audit/install-tailscale-egress.sh"
  else
    echo 'Skipped Tailscale egress install: audit nft chain is not installed yet'
  fi
fi

phase acme
# Repo-owned ACME pieces (from ops/private-access): issue/renew script,
# deploy hook, renewal units. The certificate is issued only when absent and
# the acme-dns credentials exist (archive or /etc/credstore).
run install -d -m 0755 /usr/local/lib/mag-acme /usr/local/lib/mag-private
run install -m 0755 "$ROOT/ops/private-access/issue-cert.sh" "$ROOT/ops/private-access/deploy-hook.sh" /usr/local/lib/mag-acme/
run install -m 0755 "$ROOT/ops/private-access/rollback.sh" "$ROOT/ops/private-access/confirm.sh" /usr/local/lib/mag-private/
run install -m 0644 "$ROOT/ops/private-access/mag-acme-renew.service" "$ROOT/ops/private-access/mag-acme-renew.timer" /etc/systemd/system/
run systemctl daemon-reload
if (( DRY )); then
  echo '+ issue-cert.sh production   # only when /etc/mag-nginx/tls/cert.pem is absent and acme-dns.json exists'
  echo '+ systemctl enable --now mag-acme-renew.timer   # only with acme-dns.json'
elif [[ -s /etc/mag-acme/acme-dns.json ]]; then
  if [[ ! -s /etc/mag-nginx/tls/cert.pem ]]; then
    /usr/local/lib/mag-acme/issue-cert.sh production ||
      echo 'WARN: certificate not issued; deploy will stop at the nginx preflight' >&2
  fi
  systemctl enable --now mag-acme-renew.timer
else
  echo 'WARN: no /etc/mag-acme/acme-dns.json (archive or /etc/credstore/mag-acme-dns.json); no certificate' >&2
fi

phase models
if (( STUBS )); then
  echo 'PHASE models-skipped (test stubs)'
elif (( DOWNLOAD )); then
  run bash "$ROOT/ops/backup/bootstrap-models.sh" download q4
  run bash "$ROOT/ops/backup/bootstrap-models.sh" verify q4
else
  echo 'PHASE models-skipped (pass --download-models to fetch pinned GGUF shards)'
fi

phase deploy
if (( DEPLOY )); then
  run bash "$ROOT/ops/deploy.sh"
else
  echo 'PHASE deploy-skipped (--no-deploy)'
fi

phase private-access
# deploy.sh installed the nginx vhost through deploy-nginx.sh's transaction.
# private-access/install.sh then only refreshes it (standalone) and needs an
# owner probe; it is not run unattended. Console lockdown is never applied.
note 'nginx ingress installed by deploy.sh (ops/private-access/deploy-nginx.sh); ACME units installed in phase acme'

phase pages
if (( DRY )); then
  echo "+ bash $ROOT/ops/pages/install.sh   # only when /etc/mag-pages/pages.env exists"
elif (( DEPLOY )) && [[ -r /etc/mag-pages/pages.env ]]; then
  bash "$ROOT/ops/pages/install.sh" || echo 'WARN: Pages install incomplete (bootstrap continues)' >&2
else
  echo 'PHASE pages-skipped (no /etc/mag-pages/pages.env)'
fi

phase auto-update
if (( DEPLOY )); then
  run bash "$ROOT/ops/auto-update/install.sh"
else
  echo 'PHASE auto-update-skipped (--no-deploy)'
fi

phase runners
if (( DEPLOY )); then
  if (( DRY )); then
    run bash "$ROOT/ops/runners/install.sh"
  else
    bash "$ROOT/ops/runners/install.sh" || echo 'WARN: runner install incomplete (bootstrap continues)' >&2
  fi
else
  echo 'PHASE runners-skipped (--no-deploy)'
fi

phase secrets
# Key for the encrypted bot secret store; kept if restored from the archive.
if (( DRY )); then bash "$ROOT/ops/secrets/install.sh" --dry-run; else bash "$ROOT/ops/secrets/install.sh"; fi

phase numa
# DeepSeek NUMA hot-swap is retired. No old catalog, state, sudoers or Q4
# service is seeded during bootstrap; room-model.env provides fixed routing.
echo 'PHASE numa-skipped (retired legacy model-swap)'

phase start-llm
if (( START_LLM == 0 )); then
  echo 'PHASE start-llm-skipped (no model is started or restarted)'
elif (( DRY )); then
  echo '+ systemctl start mag-llm-ds-node@0.service mag-llm-ds-node@1.service   # only if inactive'
else
  secondary_unit="mag-llm-ds-node@0.service mag-llm-ds-node@1.service"
  for unit in $secondary_unit; do
    if systemctl is-active --quiet "$unit"; then
      echo "$unit already active; not restarting"
    else
      systemctl start "$unit" || echo "$unit did not start; room deploy is otherwise complete" >&2
    fi
  done
fi

phase console-lockdown-skipped
echo 'Console lockdown stays inactive. It is not installed or enabled by bootstrap.'
echo 'Bootstrap finished.'

