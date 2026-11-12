#!/usr/bin/env bash
# mag on new hardware, from a system backup (system.sh). Run as root on a
# freshly installed Ubuntu 24.04 (same architecture), from a checkout of
# this repository, with the backup reachable:
#
#   sudo bash ops/backup/system-restore.sh --repo /mnt/mag-backup/mag-system [--snapshot ID] [--apply]
#   (a remote repository: export RESTIC_PASSWORD_FILE first)
#
# Without --apply it only shows what it would do. With it:
#  1. installs the pinned restic, reads the snapshot's host description,
#  2. puts back the apt sources and installs the packages mag had,
#  3. restores /etc (without what belongs to this machine's disks and network:
#     fstab, crypttab, netplan, the boot loader), /var/lib (without apt, dpkg
#     and snapd, which the packages own now), /home, /root, /opt, /usr/local,
#     /srv, /var/spool and /var/backups,
#  4. puts every bot VM back: its newest save becomes its disk again, its
#     definition and the network are defined and start with the host,
#  5. leaves the models to mag-modeld, which downloads and checks each one by
#     its manifest after the reboot (or they come from the backup when it was
#     made --with-models), and the Idol weights to ops/idol/install.sh.
# Then reboot. The Tailscale identity, certificates, Gitea and the room come
# back as they were, so the host keeps its name and addresses.
set -euo pipefail
[[ $EUID == 0 ]] || { echo 'root required' >&2; exit 1; }
here=$(cd -- "$(dirname -- "$(readlink -f "$0")")" && pwd)
repo= snapshot=latest apply=0
while (( $# )); do
  case $1 in
    --repo) repo=$2; shift 2 ;;
    --snapshot) snapshot=$2; shift 2 ;;
    --apply) apply=1; shift ;;
    *) echo "Usage: $0 --repo REPO [--snapshot ID] [--apply]" >&2; exit 2 ;;
  esac
done
[[ -n $repo ]] || { echo '--repo is required' >&2; exit 2; }
grep -q 'VERSION_ID="24.04"' /etc/os-release || echo 'WARNING: not Ubuntu 24.04; packages may differ' >&2
run() { echo "+ $*"; (( ! apply )) || "$@"; }
[[ -x /usr/local/bin/restic ]] || run bash "$here/install-restic-0181.sh"
restic_bin=/usr/local/bin/restic
[[ -x $restic_bin ]] || restic_bin=$(command -v restic || echo restic)
export RESTIC_REPOSITORY=$repo
nopass=()
[[ -n ${RESTIC_PASSWORD_FILE:-} ]] || nopass=(--insecure-no-password)
r() { "$restic_bin" "${nopass[@]}" "$@"; }
r snapshots --tag system --host mag
work=/var/tmp/mag-restore
install -d -m 0700 "$work"
# 1. The host description first: packages, users, VM definitions.
r restore "$snapshot" --target "$work" --include /var/lib/mag-backup/system-meta --include /etc/apt
meta=$work/var/lib/mag-backup/system-meta
[[ -s $meta/dpkg-selections.txt ]] || { echo 'snapshot has no host description' >&2; exit 1; }
echo "Snapshot of: $(head -2 "$meta/system.txt" | tr '\n' ' ')"

# 2. The packages mag had (from its own apt sources).
run cp -a "$work/etc/apt/." /etc/apt/
run apt-get update
run apt-get install -y dselect
run dpkg --clear-selections
run dpkg --set-selections < "$meta/dpkg-selections.txt"
run env DEBIAN_FRONTEND=noninteractive apt-get -y dselect-upgrade
[[ ! -s $meta/snaps.txt ]] || awk 'NR>1 {print $1}' "$meta/snaps.txt" | grep -vE '^(core[0-9]*|bare|snapd|gnome-.*|gtk-common-themes|mesa-.*)$' |
  while read -r s; do run snap install "$s" || run snap install --classic "$s" || echo "snap $s: install by hand" >&2; done

# 3. The files. Services stay off while their state comes back.
for unit in mag-120 mag-modeld mag-idol mag-scene mag-stt gitea nginx libvirtd tailscaled; do
  run systemctl stop "$unit" 2>/dev/null || :
done
run r restore "$snapshot" --target / \
  --include /etc --include /var/lib --include /home --include /root --include /opt \
  --include /usr/local --include /srv --include /var/spool --include /var/backups \
  --exclude /etc/fstab --exclude /etc/crypttab --exclude /etc/netplan --exclude /etc/default/grub \
  --exclude /etc/apt --exclude /var/lib/apt --exclude /var/lib/dpkg --exclude /var/lib/snapd \
  --exclude /var/lib/libvirt/images/magbot/bots
[[ ! -s $meta/gitea.db ]] || run install -o git -g git -m 0600 "$meta/gitea.db" /var/lib/gitea/data/gitea.db

# 4. The bot VMs: the newest save of each becomes its disk again.
saves=/var/lib/libvirt/images/magbot/saves
run systemctl start libvirtd
for xml in "$meta"/libvirt/net-*.xml; do
  [[ -e $xml ]] || continue
  n=$(basename "$xml" .xml); n=${n#net-}
  run virsh net-define "$xml" || :
  run virsh net-autostart "$n" || :
  run virsh net-start "$n" 2>/dev/null || :
done
for xml in "$meta"/libvirt/magbot-*.xml; do
  [[ -e $xml ]] || continue
  dom=$(basename "$xml" .xml); vm=${dom#magbot-}
  newest=$(ls -1 "$saves/$vm" 2>/dev/null | grep -v '^\.' | sort | tail -1 || true)
  [[ -n $newest ]] || { echo "$vm: no save; it gets a new VM from the golden image (sheet: VM erstellen)" >&2; continue; }
  dir=/var/lib/libvirt/images/magbot/bots/$vm
  run r restore "$snapshot" --target / --include "$dir"
  run qemu-img convert -O qcow2 -B /var/lib/libvirt/images/magbot/base/golden.qcow2 -F qcow2 \
    "$saves/$vm/$newest/root.qcow2" "$dir/root.qcow2"
  run chown root:kvm "$dir/root.qcow2"
  run chmod 0660 "$dir/root.qcow2"
  run virsh define "$xml"
  if grep -qx "$dom" "$meta/libvirt/autostart.txt" 2>/dev/null; then run virsh autostart "$dom"; fi
done

# 5. Models and weights: by their manifest after the reboot.
echo "Models: mag-modeld downloads and checks them after the reboot (ops/models/models.json)."
echo "Idol weights: sudo bash ops/idol/install.sh (the fetch scripts are pinned), unless the backup was made --with-models."
run systemctl daemon-reload
echo
(( apply )) && echo 'Done. Reboot now; then the room answers at its name again (MAG_DOMAIN; deploy ops/deploy.sh once if a release is missing).' ||
  echo 'Dry run. Add --apply to restore.'
