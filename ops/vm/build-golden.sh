#!/usr/bin/env bash
# Reproducible bot VM base image: pinned Ubuntu 24.04 cloud image (release
# serial + SHA256 below), customised once by cloud-init in a throwaway QEMU
# boot (user-mode network, no libvirt network needed), then written
# atomically to <MAGBOT_ROOT>/base/golden.qcow2 as root:kvm 0440.
#
# What the guest needs (mag-room/native/vm.cpp, browser.cpp, magroom.cpp,
# ops/audit/vm-proxy-setup.sh): qemu-guest-agent, cloud-init (per-bot user
# "bot" + ready marker), runuser/setsid/mktemp/python3/curl/git, Xvfb for the
# headful browser, Google Chrome stable at /opt/google/chrome/chrome (the
# AppArmor userns profile is loaded at spawn), certutil (libnss3-tools) for
# the audit CA, de_DE.UTF-8 locale, gpg/apparmor_parser, and the Chrome proxy
# policy directories. The per-host proxy policy itself is written at spawn by
# vm-proxy-setup.sh; the image ships the default from
# ops/browser-stack/live/vm-chrome-policy.json.
#
# Pinned: the cloud image. Not pinnable: Ubuntu archive updates and Google
# Chrome stable (Google's apt repo serves only the current build). Both
# versions are recorded in golden.qcow2.manifest next to the image.
#
#   sudo bash ops/vm/build-golden.sh            build only if golden.qcow2 is absent
#   sudo bash ops/vm/build-golden.sh --force    rebuild (refused while bot overlays exist)
#   bash ops/vm/build-golden.sh --print-pin     show the pinned image
# Env: MAGBOT_ROOT (default /var/lib/libvirt/images/magbot),
#      MAG_GOLDEN_CACHE (download cache, default /var/cache/mag-golden),
#      MAG_GOLDEN_TIMEOUT (seconds for the customise boot, default 3600),
#      MAG_GOLDEN_ACCEL=tcg (no KVM; several times slower).
# Never touches running bot VMs, their overlays, mag-llm or the room.
set -euo pipefail
HERE=$(cd -- "$(dirname -- "$(readlink -f "$0")")" && pwd)
ROOT_REPO=$(cd -- "$HERE/../.." && pwd)
UBUNTU_SERIAL=20261017
UBUNTU_IMAGE=ubuntu-24.04-server-cloudimg-amd64.img
UBUNTU_URL=https://cloud-images.ubuntu.com/releases/noble/release-$UBUNTU_SERIAL/$UBUNTU_IMAGE
# From https://cloud-images.ubuntu.com/releases/noble/release-20261017/SHA256SUMS
UBUNTU_SHA256=04e8f2461a16a44e78a370284471f29616cdd9baf1893df25807b71f985b6c94
GOLDEN_VIRTUAL_SIZE=12G   # bot overlays are >= 20 GiB (vm.cpp default_disk_gib)
MAGBOT_ROOT=${MAGBOT_ROOT:-/var/lib/libvirt/images/magbot}
CACHE=${MAG_GOLDEN_CACHE:-/var/cache/mag-golden}
TIMEOUT=${MAG_GOLDEN_TIMEOUT:-3600}
FORCE=0
case "${1:-}" in
  --force) FORCE=1 ;;
  --print-pin) printf '%s %s\n' "$UBUNTU_URL" "$UBUNTU_SHA256"; exit 0 ;;
  '') ;;
  *) echo "usage: $0 [--force|--print-pin]" >&2; exit 2 ;;
esac
[[ $EUID == 0 ]] || { echo 'root required' >&2; exit 1; }
GOLDEN="$MAGBOT_ROOT/base/golden.qcow2"
log() { echo "golden: $*"; }

if [[ -e $GOLDEN && $FORCE == 0 ]]; then
  log "$GOLDEN exists; left unchanged (use --force to rebuild)"
  exit 0
fi
if [[ -e $GOLDEN ]]; then
  # Overlays reference golden.qcow2 by path; new content under them corrupts them.
  if compgen -G "$MAGBOT_ROOT/bots/*/root.qcow2" >/dev/null; then
    echo "golden: bot overlays exist under $MAGBOT_ROOT/bots; refusing to replace their backing image" >&2
    exit 1
  fi
fi
for tool in qemu-system-x86_64 qemu-img cloud-localds sha256sum curl; do
  command -v "$tool" >/dev/null || { echo "golden: $tool missing (apt-get install qemu-system-x86 qemu-utils cloud-image-utils)" >&2; exit 1; }
done
# MAG_GOLDEN_ACCEL=tcg forces software emulation (slow; for hosts without a
# usable /dev/kvm, e.g. CI or nested test boxes).
accel=(-machine q35 -accel kvm -cpu host)
if [[ ${MAG_GOLDEN_ACCEL:-kvm} == tcg ]]; then
  accel=(-machine q35 -accel 'tcg,thread=multi' -cpu max)
elif [[ ! -w /dev/kvm ]]; then
  echo 'golden: /dev/kvm unavailable (MAG_GOLDEN_ACCEL=tcg builds slowly without KVM)' >&2
  exit 1
fi

install -d -m 0755 "$CACHE" "$MAGBOT_ROOT" "$MAGBOT_ROOT/base"
base="$CACHE/noble-$UBUNTU_SERIAL.img"
if [[ ! -s $base ]] || ! printf '%s  %s\n' "$UBUNTU_SHA256" "$base" | sha256sum -c --quiet - >/dev/null 2>&1; then
  log "downloading $UBUNTU_URL"
  curl -fL --retry 3 --connect-timeout 20 "$UBUNTU_URL" -o "$base.part"
  printf '%s  %s\n' "$UBUNTU_SHA256" "$base.part" | sha256sum -c --quiet - ||
    { rm -f "$base.part"; echo 'golden: cloud image SHA256 mismatch' >&2; exit 1; }
  mv -f "$base.part" "$base"
fi
log "cloud image $UBUNTU_SERIAL verified"

work=$(mktemp -d "$MAGBOT_ROOT/base/.golden-build.XXXXXXXX")
cleanup() { rm -rf -- "$work"; }
trap cleanup EXIT
qemu-img convert -f qcow2 -O qcow2 "$base" "$work/disk.qcow2"
qemu-img resize -q "$work/disk.qcow2" "$GOLDEN_VIRTUAL_SIZE"

policy=$(sed 's/^/      /' "$ROOT_REPO/ops/browser-stack/live/vm-chrome-policy.json")
commit=$(git -c safe.directory="$ROOT_REPO" -C "$ROOT_REPO" rev-parse --short=12 HEAD 2>/dev/null || echo unknown)
cat > "$work/user-data" <<EOF
#cloud-config
# MAG golden image customisation (ops/vm/build-golden.sh, repo $commit)
hostname: mag-golden
ssh_pwauth: false
disable_root: true
users: []
timezone: Europe/Berlin
locale: de_DE.UTF-8
package_update: true
package_upgrade: true
packages:
  - qemu-guest-agent
  - cloud-init
  - curl
  - ca-certificates
  - git
  - gnupg
  - jq
  - python3
  - python3-venv
  - python3-pip
  - build-essential
  - unzip
  - xvfb
  - xauth
  - libnss3-tools
  - fonts-liberation
  - fonts-dejavu-core
  - fonts-noto-color-emoji
  - locales
  - apparmor
  - apparmor-utils
write_files:
  - path: /etc/opt/chrome/policies/managed/mag-audit.json
    permissions: '0644'
    content: |
$policy
  - path: /etc/chromium/policies/managed/mag-audit.json
    permissions: '0644'
    content: |
$policy
runcmd:
  - [sh, -c, 'locale-gen de_DE.UTF-8 en_US.UTF-8']
  - [sh, -c, 'install -d -m 0755 /etc/apt/keyrings /opt/magbot/browsers /var/lib/magbot']
  - [sh, -c, 'curl -fsSL https://dl.google.com/linux/linux_signing_key.pub | gpg --dearmor --yes -o /etc/apt/keyrings/google-chrome.gpg']
  - [sh, -c, 'echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/google-chrome.gpg] https://dl.google.com/linux/chrome/deb/ stable main" > /etc/apt/sources.list.d/google-chrome.list']
  - [sh, -c, 'apt-get update -q && DEBIAN_FRONTEND=noninteractive apt-get install -y -q google-chrome-stable']
  - [sh, -c, 'test -x /opt/google/chrome/chrome && test -x /usr/bin/Xvfb && test -x /usr/bin/certutil && test -x /usr/sbin/qemu-ga']
  - [systemctl, enable, qemu-guest-agent]
  - [sh, -c, 'rm -f /var/lib/magbot/ready']
  - [sh, -c, '{ echo "golden_built=\$(date -u +%FT%TZ) repo=$commit ubuntu_serial=$UBUNTU_SERIAL"; /opt/google/chrome/chrome --version; dpkg-query -W -f "\\\${Package} \\\${Version}\\n"; } > /etc/mag-golden-manifest.txt']
  - [sh, -c, 'echo "MAG-GOLDEN-CHROME \$(/opt/google/chrome/chrome --version)" > /dev/ttyS0']
  - [sh, -c, 'apt-get clean && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*']
  - [sh, -c, 'rm -f /etc/ssh/ssh_host_* && truncate -s 0 /etc/machine-id && rm -f /var/lib/dbus/machine-id']
  - [sh, -c, 'cloud-init clean --logs --seed']
  - [sh, -c, 'sync && fstrim -av || true']
  - [sh, -c, 'echo MAG-GOLDEN-BUILD-OK > /dev/ttyS0']
power_state:
  mode: poweroff
  delay: now
  condition: true
EOF
printf 'instance-id: mag-golden-%s\nlocal-hostname: mag-golden\n' "$UBUNTU_SERIAL" > "$work/meta-data"
cloud-localds "$work/seed.iso" "$work/user-data" "$work/meta-data"

log "customising (QEMU, up to ${TIMEOUT}s; console log $work/console.log)"
rc=0
timeout "$TIMEOUT" qemu-system-x86_64 "${accel[@]}" -m 4096 -smp "$(( $(nproc) < 4 ? $(nproc) : 4 ))" \
  -display none -no-reboot -serial "file:$work/console.log" -monitor none \
  -drive "file=$work/disk.qcow2,if=virtio,format=qcow2" \
  -drive "file=$work/seed.iso,if=virtio,format=raw,readonly=on" \
  -netdev user,id=n0 -device virtio-net-pci,netdev=n0 || rc=$?
if (( rc != 0 )) || ! grep -q 'MAG-GOLDEN-BUILD-OK' "$work/console.log"; then
  echo "golden: customisation failed (qemu rc=$rc). Last console lines:" >&2
  tail -n 40 "$work/console.log" >&2 || true
  exit 1
fi
chrome=$(grep -ao 'MAG-GOLDEN-CHROME .*' "$work/console.log" | tail -1 | tr -d '\r' | sed 's/^MAG-GOLDEN-CHROME //')

# Plain re-write drops unused clusters; the result has no backing file.
qemu-img convert -f qcow2 -O qcow2 "$work/disk.qcow2" "$work/golden.qcow2"
qemu-img check -q "$work/golden.qcow2"
size=$(stat -c %s "$work/golden.qcow2")
(( size >= 100 * 1024 * 1024 )) || { echo "golden: result too small ($size bytes)" >&2; exit 1; }
sum=$(sha256sum "$work/golden.qcow2" | cut -d' ' -f1)
{
  echo "built=$(date -u +%FT%TZ)"
  echo "repo_commit=$commit"
  echo "ubuntu_url=$UBUNTU_URL"
  echo "ubuntu_sha256=$UBUNTU_SHA256"
  echo "chrome=$chrome"
  echo "golden_sha256=$sum"
} > "$work/golden.qcow2.manifest"
kvm_group=root
getent group kvm >/dev/null && kvm_group=kvm
chown "root:$kvm_group" "$work/golden.qcow2"
chmod 0440 "$work/golden.qcow2"
chmod 0644 "$work/golden.qcow2.manifest"
mv -f "$work/golden.qcow2.manifest" "$GOLDEN.manifest"
mv -f "$work/golden.qcow2" "$GOLDEN"
log "wrote $GOLDEN (sha256 $sum, $chrome)"
