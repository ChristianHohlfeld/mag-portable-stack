#!/usr/bin/env bash
# Install on MAG host only. Does not reboot or modify existing MAG containers.
set -euo pipefail
# virsh status labels vary with the host locale (e.g. Aktiv: ja in German).
export LC_ALL=C
if [[ "${EUID}" -ne 0 ]]; then echo "Run: sudo bash install-magbot.sh" >&2; exit 1; fi
ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
[[ -f "$ROOT_DIR/magbot.py" ]] || { echo 'magbot.py must be beside installer' >&2; exit 1; }
[[ -e /dev/kvm ]] || { echo 'STOP: /dev/kvm not found; verify post-reboot VT-x first' >&2; exit 1; }
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
  qemu-kvm libvirt-daemon-system libvirt-clients virtinst qemu-utils cloud-image-utils curl python3
systemctl enable --now libvirtd.service || { echo 'libvirtd unavailable; inspect libvirt socket services' >&2; exit 1; }
install -d -m 0711 /var/lib/libvirt/images/magbot /var/lib/libvirt/images/magbot/bots
install -d -m 0700 /etc/magbot
install -d -m 0755 /usr/local/libexec
install -m 0755 "$ROOT_DIR/magbot.py" /usr/local/libexec/magbot
ln -sfn /usr/local/libexec/magbot /usr/local/bin/magbot
ln -sfn /usr/local/bin/magbot /usr/local/bin/spawnbot
ln -sfn /usr/local/bin/magbot /usr/local/bin/despawnbot
if [[ ! -e /etc/magbot/config.json ]]; then
  cat >/etc/magbot/config.json <<'JSON'
{
  "max_bots": 4,
  "max_total_vcpus": 8,
  "max_total_ram_gib": 24,
  "min_host_available_ram_gib": 64,
  "min_host_free_disk_gib": 100,
  "max_host_load_per_cpu": 0.75,
  "default_vcpus": 2,
  "default_ram_gib": 4,
  "default_disk_gib": 20,
  "max_bot_vcpus": 4,
  "max_bot_ram_gib": 8,
  "max_bot_disk_gib": 32,
  "disk_bytes_sec": 40000000,
  "disk_iops_sec": 400,
  "network": "default",
  "base_url": "https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img"
}
JSON
  chmod 0600 /etc/magbot/config.json
fi
# Dedicated libvirt NAT network; do not touch any Docker networks.
if ! virsh -c qemu:///system net-info default >/dev/null 2>&1; then
  if [[ -f /usr/share/libvirt/networks/default.xml ]]; then
    virsh -c qemu:///system net-define /usr/share/libvirt/networks/default.xml
  else
    echo 'Default libvirt NAT network missing and no packaged definition present' >&2
    exit 1
  fi
fi
if ! virsh -c qemu:///system net-info default | grep -Eq '^Active:\s+yes'; then
  # Another service may have started the default NAT network concurrently.
  if ! virsh -c qemu:///system net-start default; then
    virsh -c qemu:///system net-info default | grep -Eq '^Active:[[:space:]]+yes' || {
      echo 'Failed to activate libvirt default network' >&2
      exit 1
    }
  fi
fi
virsh -c qemu:///system net-autostart default
/usr/local/bin/magbot doctor
/usr/local/bin/magbot image-update
printf '\nMAGBOT INSTALLED. Commands:\n'
printf '  sudo magbot spawn bot1\n  sudo magbot status bot1\n  sudo magbot exec bot1 -- /opt/magbot/venv/bin/python /opt/magbot/health.py\n  sudo magbot despawn bot1\n'
