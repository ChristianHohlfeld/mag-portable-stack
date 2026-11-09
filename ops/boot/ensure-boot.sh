#!/usr/bin/env bash
# Reboot resistance (root, idempotent; runs after every deploy). Rule: what
# runs now must come back after a power cut. It only enables units for the
# next boot; it never starts, stops or restarts anything.
# Models are not listed: mag-modeld is the single owner of their lifecycle
# and starts each verified model by itself after boot.
set -euo pipefail
[[ $EUID == 0 ]] || { echo 'root required' >&2; exit 1; }
log() { echo "boot: $*"; }
agents_json=${MAG_AGENTS_JSON:-/var/lib/magbot/room/agents.json}
units=(
  mag-modeld.service
  mag-audit-nft.service mag-audit-gateway.service mag-audit-webproxy.service
  mag-120.service mag-agents-network.service mag-browser-webrtc.service mag-git-gateway.socket
  mag-idol.service mag-bots-publish.service mag-cpu-tune.service
  gitea.service nginx.service tailscaled.service libvirtd.service ssh.service
  mag-gitea-runner.service mag-gitea-funnel.service mag-gitea-funnel.timer
  mag-tailscale-cert.timer mag-tailscale-egress.timer mag-acme-renew.timer
  mag-stack-check.timer mag-agent-drift.timer
  mag-backup-daily.timer mag-backup-weekly.timer mag-backup-monthly.timer
  mag-backup-monitor.timer mag-backup-restore-test.timer mag-backup-clear.timer
)
for u in "${units[@]}"; do
  systemctl is-active --quiet "$u" || continue
  state="$(systemctl is-enabled "$u" 2>/dev/null || true)"
  case "$state" in
    enabled|enabled-runtime|alias|indirect|generated|transient) ;;
    static|disabled|"")
      if [[ $u == *.timer ]]; then target=timers.target
      elif [[ $u == *.socket ]]; then target=sockets.target
      else target=multi-user.target; fi
      if [[ $state == static ]]; then systemctl add-wants "$target" "$u"; else systemctl enable "$u"; fi
      log "enabled $u for boot" ;;
    *) log "left $u as $state" ;;
  esac
done

# Bot VMs: the VMs of the room's bots come back on boot, others stay off.
if command -v virsh >/dev/null && command -v jq >/dev/null && [[ -r $agents_json ]]; then
  wanted=$(jq -r '.[] | select(.enabled != false) | .vm // empty | select(test("^agent[0-9]+$")) | "magbot-"+.' "$agents_json" 2>/dev/null || true)
  for d in $(virsh -q list --all --name 2>/dev/null | grep '^magbot-' || true); do
    if grep -Fxq "$d" <<<"$wanted"; then
      virsh -q autostart "$d" >/dev/null && log "autostart $d"
    fi
  done
  for n in $(virsh -q net-list --name 2>/dev/null || true); do virsh -q net-autostart "$n" >/dev/null || true; done
fi
log done
