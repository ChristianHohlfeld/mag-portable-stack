#!/usr/bin/env bash
# Blank Ubuntu host -> running MAG stack from one verified full archive.
# Does not enable console lockdown. Does not restart mag-llm.
#   bash ops/bootstrap.sh --dry-run
#   sudo bash ops/bootstrap.sh --archive /var/backups/mag-full/<id> [--download-models] [--start-llm]
set -euo pipefail
ROOT=$(cd -- "$(dirname -- "$(readlink -f "$0")")/.." && pwd)
# shellcheck source=build-targets.sh
source "$ROOT/ops/build-targets.sh"
DRY=0
ARCHIVE=
DOWNLOAD=0
START_LLM=0
DEPLOY=1
while (( $# )); do
  case "$1" in
    --dry-run) DRY=1; shift ;;
    --archive) ARCHIVE=${2:-}; shift 2 ;;
    --download-models) DOWNLOAD=1; shift ;;
    --start-llm) START_LLM=1; shift ;;
    --no-deploy) DEPLOY=0; shift ;;
    -h|--help)
      echo "Usage: $0 --dry-run | --archive DIR [--download-models] [--start-llm] [--no-deploy]"
      exit 0 ;;
    *) echo "Unknown argument $1" >&2; exit 2 ;;
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

if (( DRY == 0 )); then
  [[ $EUID == 0 ]] || { echo 'Root required (or pass --dry-run)' >&2; exit 1; }
  [[ -n "$ARCHIVE" && -f "$ARCHIVE/VERIFIED" && -d "$ARCHIVE/repo" && ! -L "$ARCHIVE" ]] || {
    echo 'Pass --archive DIR for one verified /var/backups/mag-full directory' >&2; exit 1;
  }
  if systemctl is-active --quiet mag-120.service || systemctl is-active --quiet mag-llm.service; then
    echo 'mag-120 or mag-llm is already active. Use ops/deploy.sh. Bootstrap is for a blank host.' >&2
    exit 1
  fi
fi

phase packages
run apt-get update
run env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
  build-essential cmake ninja-build g++ pkg-config \
  libssl-dev libcurl4-openssl-dev libarchive-dev \
  nginx jq sqlite3 numactl git curl ca-certificates openssl python3 \
  bzip2 dnsutils acl golang-go

phase restic
run bash "$ROOT/ops/backup/install-restic-0181.sh"

phase users
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

phase restore
STAGE=${MAG_BOOTSTRAP_STAGE:-/var/tmp/mag-bootstrap-restore}
if (( DRY )); then
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
  if [[ -e "$dest" && "${MAG_BOOTSTRAP_REPLACE:-0}" != 1 ]]; then
    echo "Leave existing $dest"
    return 0
  fi
  install -d -m 0755 "$(dirname "$dest")"
  cp -a -- "$from" "$dest"
}

phase place-state
for rel in var/lib/magbot/room var/lib/gitea etc/gitea var/lib/mag-pages etc/mag-pages \
  etc/mag-acme etc/mag-nginx etc/nginx etc/magbot etc/default/mag-room etc/mag-stack \
  etc/credstore/mag-guest-oauth etc/mag-audit var/lib/mag-audit etc/systemd/system \
  home/chris/llama-templates var/spool/cron/crontabs etc/cron.d; do
  place "$rel"
done

phase llm-env
if (( DRY )); then
  echo '+ install llm.env only when /etc/mag-llm/llm.env is absent'
else
  if [[ ! -e /etc/mag-llm/llm.env ]]; then
    install -d -m 0750 /etc/mag-llm
    install -m 0640 -o root -g root "$ROOT/ops/deepseek/llm.env.mag" /etc/mag-llm/llm.env
  else
    echo 'Left existing /etc/mag-llm/llm.env unchanged'
  fi
fi

phase tailscale
if (( DRY )); then
  echo '+ install tailscale from pkgs.tailscale.com when the binary is absent; do not upgrade an existing tailscaled'
  echo "+ place var/lib/tailscale after systemctl stop tailscaled"
else
  if ! command -v tailscale >/dev/null 2>&1; then
    curl -fsSL https://pkgs.tailscale.com/stable/ubuntu/noble.noarmor.gpg -o /usr/share/keyrings/tailscale-archive-keyring.gpg
    curl -fsSL https://pkgs.tailscale.com/stable/ubuntu/noble.list -o /etc/apt/sources.list.d/tailscale.list
    apt-get update
    env DEBIAN_FRONTEND=noninteractive apt-get install -y tailscale
  else
    echo "Tailscale already installed: $(tailscale version | head -1)"
  fi
  if [[ -e "$STAGE/var/lib/tailscale" ]]; then
    systemctl stop tailscaled.service || true
    if [[ ! -e /var/lib/tailscale || "${MAG_BOOTSTRAP_REPLACE:-0}" == 1 ]]; then
      rm -rf -- /var/lib/tailscale
      cp -a -- "$STAGE/var/lib/tailscale" /var/lib/tailscale
    fi
    systemctl enable --now tailscaled.service
  else
    systemctl enable --now tailscaled.service
    echo 'No Tailscale state in the archive. tailscale up still needs an auth key on this host.' >&2
  fi
fi

phase gitea
run bash "$ROOT/ops/gitea/install.sh"

phase build
run cmake -S "$ROOT" -B "$ROOT/build" -G Ninja -DCMAKE_BUILD_TYPE=Release
run cmake --build "$ROOT/build" -j "$(nproc)" --target "${MAG_BUILD_TARGETS[@]}"
if (( DRY )); then
  echo '+ go build -mod=mod -o build/mag-browser-webrtc ./mag-room/webrtc'
else
  if [[ ! -x "$ROOT/build/mag-browser-webrtc" ]]; then
    (cd "$ROOT/mag-room/webrtc" && go build -mod=mod -o "$ROOT/build/mag-browser-webrtc" .)
  fi
fi

phase backup-install
run bash "$ROOT/ops/backup/install.sh"

phase gateway
run install -m 0644 "$ROOT/ops/audit/systemd/mag-audit-gateway.service" /etc/systemd/system/mag-audit-gateway.service
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

phase models
if (( DOWNLOAD )); then
  run bash "$ROOT/ops/backup/bootstrap-models.sh" download all
  run bash "$ROOT/ops/backup/bootstrap-models.sh" verify all
else
  echo 'PHASE models-skipped (pass --download-models to fetch pinned GGUF shards)'
fi

phase deploy
if (( DEPLOY )); then
  run bash "$ROOT/ops/deploy.sh"
else
  echo 'PHASE deploy-skipped (--no-deploy)'
fi

phase start-llm
if (( START_LLM == 0 )); then
  echo 'PHASE start-llm-skipped (mag-llm is not started or restarted)'
elif (( DRY )); then
  echo '+ systemctl start mag-llm.service and the configured NUMA1 model   # only if inactive'
else
  if systemctl is-active --quiet mag-llm.service; then
    echo 'mag-llm already active; not restarting'
  else
    systemctl start mag-llm.service
  fi
  secondary_unit=mag-llm-iq3.service
  if [[ -r /etc/mag-stack/secondary-model.env ]] &&
     grep -Fxq 'MAG_SECONDARY_MODEL=qwen' /etc/mag-stack/secondary-model.env; then
    secondary_unit=mag-llm-qwen.service
  fi
  if systemctl is-active --quiet "$secondary_unit"; then
    echo "$secondary_unit already active; not restarting"
  else
    systemctl start "$secondary_unit" || echo "$secondary_unit did not start; room deploy is otherwise complete" >&2
  fi
fi

phase console-lockdown-skipped
echo 'Console lockdown stays inactive. It is not installed or enabled by bootstrap.'
echo 'Bootstrap finished.'
