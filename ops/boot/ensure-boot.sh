#!/usr/bin/env bash
# Reboot resistance (root, idempotent; runs after every deploy). Rule: what runs
# now must come back after a power cut. It only enables units for the next boot;
# it never starts, stops, restarts or reloads anything, so the models stay in RAM.
set -euo pipefail
[[ $EUID == 0 ]] || { echo 'root required' >&2; exit 1; }
log() { echo "boot: $*"; }
# The active NUMA1 service is persisted after explicit operator cutover;
# never re-enable the retired IQ3 on a Qwen host during deployment.
secondary_unit=mag-llm-iq3.service
secondary_units=()
if [[ -r /etc/mag-stack/secondary-model.env ]] &&
   grep -Fxq 'MAG_SECONDARY_MODEL=ornith' /etc/mag-stack/secondary-model.env; then
  # Ornith host: 4 instances own NUMA0+1 cores; never boot Qwen or IQ3 next to them.
  secondary_unit=mag-llm-ornith@1.service
  secondary_units=(mag-llm-ornith@2.service mag-llm-ornith@3.service mag-llm-ornith@4.service)
  systemctl disable mag-llm-iq3.service mag-llm-qwen.service >/dev/null 2>&1 || true
  log 'Ornith x4 host: Qwen/IQ3 stay disabled for boot'
elif [[ -r /etc/mag-stack/secondary-model.env ]] &&
   grep -Fxq 'MAG_SECONDARY_MODEL=qwen' /etc/mag-stack/secondary-model.env; then
  secondary_unit=mag-llm-qwen.service
  # An old static IQ3 unit may still have a manually created boot wants link.
  # Never boot IQ3 and Qwen together on NUMA1; do not stop a running model.
  systemctl disable mag-llm-iq3.service >/dev/null 2>&1 || true
  legacy_link=/etc/systemd/system/multi-user.target.wants/mag-llm-iq3.service
  if [[ -L $legacy_link && $(basename "$(readlink "$legacy_link")") == mag-llm-iq3.service ]]; then
    rm -f -- "$legacy_link"
    log 'retired legacy IQ3 boot link'
  fi
  # Idempotent even if an older staged Qwen unit lacked [Install].
  systemctl add-wants multi-user.target mag-llm-qwen.service
  log 'Qwen NUMA1 enabled for every boot; Q4 readiness gate installed by deploy'
fi
units=(
  mag-llm.service "$secondary_unit" "${secondary_units[@]}"
  mag-audit-nft.service mag-audit-gateway.service mag-audit-webproxy.service
  mag-120.service mag-agents-network.service mag-browser-webrtc.service mag-git-gateway.socket
  gitea.service nginx.service tailscaled.service libvirtd.service ssh.service
  mag-gitea-runner.service mag-github-runner.service mag-deploy-request.path
  mag-auto-update.timer mag-stack-check.timer mag-agent-drift.timer mag-tailscale-egress.timer mag-acme-renew.timer
  mag-backup-daily.timer mag-backup-weekly.timer mag-backup-monthly.timer
  mag-backup-monitor.timer mag-backup-restore-test.timer mag-backup-clear.timer
)
for u in "${units[@]}"; do
  systemctl is-active --quiet "$u" || continue
  state="$(systemctl is-enabled "$u" 2>/dev/null || true)"
  case "$state" in
    enabled|enabled-runtime|alias|indirect|generated|transient) ;;
    static|disabled|"")
      # Units without [Install] (e.g. mag-llm-iq3) are pulled in by a wants link.
      if [[ $u == *.timer ]]; then target=timers.target
      elif [[ $u == *.path ]]; then target=paths.target
      elif [[ $u == *.socket ]]; then target=sockets.target
      else target=multi-user.target; fi
      if [[ $state == static ]]; then systemctl add-wants "$target" "$u"; else systemctl enable "$u"; fi
      log "enabled $u for boot" ;;
    *) log "left $u as $state" ;;
  esac
done

# Bot VMs and their network come back on boot.
if command -v virsh >/dev/null; then
  if [[ ( $secondary_unit == mag-llm-qwen.service || $secondary_unit == mag-llm-ornith@1.service ) &&
        -r /var/lib/magbot/room/agents.json ]] && command -v jq >/dev/null; then
    # The first two persisted agents are the active NUMA bots; PARKED agents
    # retain data but must NOT bring surplus KVM guests back on cold boot.
    wanted=$(jq -r '.[0:2][] | .vm // empty | select(test("^agent[0-9]+$")) | "magbot-"+.' /var/lib/magbot/room/agents.json)
    for d in $(virsh -q list --all --name 2>/dev/null | grep '^magbot-' || true); do
      if grep -Fxq "$d" <<<"$wanted"; then
        virsh -q autostart "$d" >/dev/null && log "autostart $d"
      else
        virsh -q autostart --disable "$d" >/dev/null && log "parked VM will not autostart: $d"
      fi
    done
  else
    for d in $(virsh -q list --name 2>/dev/null | grep '^magbot-' || true); do
      virsh -q autostart "$d" >/dev/null && log "autostart $d"
    done
  fi
  for n in $(virsh -q net-list --name 2>/dev/null || true); do virsh -q net-autostart "$n" >/dev/null || true; done
fi

# Power back on after a power cut (needs a BMC; otherwise set "Restore on AC
# power loss: Power On" once in the BIOS).
if command -v ipmitool >/dev/null && [[ -e /dev/ipmi0 || -e /dev/ipmi/0 ]]; then
  ipmitool chassis policy always-on >/dev/null && log 'BMC power policy: always-on'
else
  log 'no BMC found; set "Restore on AC power loss: Power On" in the BIOS once'
fi
log done
