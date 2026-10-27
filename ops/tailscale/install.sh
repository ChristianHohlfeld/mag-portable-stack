#!/usr/bin/env bash
# Tailscale on the MAG host (called by ops/bootstrap.sh, phase tailscale).
#  - Ubuntu release check: the package source follows /etc/os-release; MAG is
#    built and tested on Ubuntu 24.04 (noble). Other releases need
#    MAG_ALLOW_OTHER_RELEASE=1.
#  - Installs tailscale from pkgs.tailscale.com when absent; never upgrades or
#    restarts an existing, logged-in tailscaled.
#  - --state DIR: restore var/lib/tailscale from a backup stage (same node key
#    => same Tailnet IP 100.123.114.16 that nginx and Pages listen on).
#  - No restored state and not logged in: if /etc/credstore/tailscale.authkey
#    exists (root-owned, 0600/0400) run
#      tailscale up --auth-key file:/etc/credstore/tailscale.authkey [args]
#    The key is passed by file name only and never printed. Extra non-secret
#    flags may be listed one per line in /etc/mag-stack/tailscale-up.args.
#   sudo bash ops/tailscale/install.sh [--state DIR] [--dry-run]
set -euo pipefail
STATE=
DRY=0
while (( $# )); do
  case "$1" in
    --state) STATE=${2:-}; shift 2 ;;
    --dry-run) DRY=1; shift ;;
    *) echo "usage: $0 [--state DIR] [--dry-run]" >&2; exit 2 ;;
  esac
done
KEY=${MAG_TAILSCALE_AUTHKEY_FILE:-/etc/credstore/tailscale.authkey}
EXPECTED_IP=100.123.114.16
log() { echo "tailscale: $*"; }

# shellcheck disable=SC1091
. /etc/os-release
codename=${VERSION_CODENAME:-}
if [[ ${ID:-} != ubuntu || $codename != noble ]]; then
  if [[ ${MAG_ALLOW_OTHER_RELEASE:-0} != 1 ]]; then
    echo "tailscale: MAG targets Ubuntu 24.04 (noble); this host is ${PRETTY_NAME:-unknown}. Set MAG_ALLOW_OTHER_RELEASE=1 to continue anyway." >&2
    exit 1
  fi
  log "WARN: unsupported release ${PRETTY_NAME:-unknown}; continuing on request"
fi
distro=${ID:-ubuntu}
if (( DRY )); then
  echo "+ install tailscale from https://pkgs.tailscale.com/stable/$distro/$codename when absent (no upgrade)"
  echo "+ restore var/lib/tailscale from the stage when present, then enable tailscaled"
  echo "+ tailscale up --auth-key file:$KEY   # only without restored state, when not logged in and the key file exists"
  exit 0
fi
[[ $EUID == 0 ]] || { echo 'root required' >&2; exit 1; }

if ! command -v tailscale >/dev/null 2>&1; then
  curl -fsSL "https://pkgs.tailscale.com/stable/$distro/$codename.noarmor.gpg" -o /usr/share/keyrings/tailscale-archive-keyring.gpg
  curl -fsSL "https://pkgs.tailscale.com/stable/$distro/$codename.tailscale-keyring.list" -o /etc/apt/sources.list.d/tailscale.list
  apt-get update
  env DEBIAN_FRONTEND=noninteractive apt-get install -y tailscale
else
  log "already installed: $(tailscale version | head -1)"
fi

restored=0
if [[ -n $STATE && -d $STATE/var/lib/tailscale ]]; then
  if [[ ! -e /var/lib/tailscale/tailscaled.state || ${MAG_BOOTSTRAP_REPLACE:-0} == 1 ]]; then
    systemctl stop tailscaled.service 2>/dev/null || true
    rm -rf -- /var/lib/tailscale
    cp -a -- "$STATE/var/lib/tailscale" /var/lib/tailscale
    restored=1
    log 'restored node state from the backup stage'
  else
    log 'existing /var/lib/tailscale state kept'
  fi
fi
systemctl enable --now tailscaled.service

backend() { tailscale status --json 2>/dev/null | jq -r '.BackendState // empty' 2>/dev/null || true; }
for _ in $(seq 1 20); do [[ -n $(backend) ]] && break; sleep 1; done
state=$(backend)
if [[ $state == Running ]]; then
  log 'logged in'
elif (( restored )); then
  log "restored state, backend $state; check 'tailscale status'"
elif [[ -f $KEY && ! -L $KEY ]]; then
  [[ $(stat -c %u "$KEY") == 0 ]] || { echo "tailscale: $KEY must be root-owned" >&2; exit 1; }
  case $(stat -c %a "$KEY") in 600|400) ;; *) echo "tailscale: $KEY must be mode 0600 or 0400" >&2; exit 1 ;; esac
  args=()
  if [[ -r /etc/mag-stack/tailscale-up.args ]]; then
    while IFS= read -r line; do
      [[ -z $line || $line == \#* ]] && continue
      [[ $line =~ ^--[a-z-]+(=[A-Za-z0-9._:,/@-]*)?$ ]] || { echo "tailscale: refusing flag line in tailscale-up.args" >&2; exit 1; }
      [[ $line != --auth-key* && $line != --authkey* ]] || { echo 'tailscale: auth key belongs in the credstore file' >&2; exit 1; }
      args+=("$line")
    done < /etc/mag-stack/tailscale-up.args
  fi
  tailscale up --auth-key "file:$KEY" "${args[@]}"
  log 'joined the tailnet with the credstore auth key'
else
  echo "tailscale: not logged in and no $KEY; place a key there (root, 0600) and rerun, or run 'tailscale up' manually." >&2
fi
ip4=$(tailscale ip -4 2>/dev/null | head -1 || true)
if [[ -n $ip4 && $ip4 != "$EXPECTED_IP" ]]; then
  echo "tailscale: WARN Tailnet IP is $ip4, but clients and DNS use $EXPECTED_IP (pages.env)." >&2
  echo "           Restore the old node state from the backup. Do not point nginx at this address; tailscaled owns :443." >&2
fi
exit 0
