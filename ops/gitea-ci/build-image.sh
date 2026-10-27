#!/usr/bin/env bash
# First-run only, under mag-ci-workload.slice + the same inference-idle guard.
set -euo pipefail
[[ $EUID == 0 ]] || exit 1
[[ ! -s /var/lib/mag-ci/base.qcow2 ]] || exit 0
systemctl is-active --quiet mag-ci-vm.service && exit 1
here=/usr/local/lib/mag-ci
work=$(mktemp -d /var/lib/mag-ci/image-build.XXXXXXXX)
trap 'rm -rf "$work"' EXIT
base=https://cloud-images.ubuntu.com/noble/current
for f in SHA256SUMS SHA256SUMS.gpg noble-server-cloudimg-amd64.img; do
 curl --proto '=https' --tlsv1.2 -fsSL --retry 3 --max-time 1200 "$base/$f" -o "$work/$f"
done
gpgv --keyring /usr/share/keyrings/ubuntu-cloudimage-keyring.gpg "$work/SHA256SUMS.gpg" "$work/SHA256SUMS"
(cd "$work"; grep ' noble-server-cloudimg-amd64.img$' SHA256SUMS | sha256sum -c -)
qemu-img convert -f qcow2 -O qcow2 "$work/noble-server-cloudimg-amd64.img" "$work/base.qcow2"
qemu-img resize "$work/base.qcow2" 64G >/dev/null
cat > "$work/user-data" <<'CLOUD'
#cloud-config
ssh_pwauth: false
disable_root: true
users: []
package_update: true
packages:
  - build-essential
  - cmake
  - ninja-build
  - libssl-dev
  - libcurl4-openssl-dev
  - libarchive-dev
  - libicu-dev
  - libsqlite3-dev
  - python3
  - python3-venv
  - python3-pip
  - nodejs
  - npm
  - golang-go
  - git
  - git-lfs
  - nginx
  - openssl
  - jq
  - curl
  - wget
  - ca-certificates
  - dnsutils
  - sqlite3
  - shellcheck
  - sudo
  - bzip2
  - xz-utils
  - util-linux
  - iproute2
  - nftables
  - podman
  - uidmap
  - fuse-overlayfs
  - slirp4netns
  - dbus
write_files:
CLOUD
# Non-secret runtime binaries/templates, never controller/source/status tokens.
add_file() { printf '  - path: %s\n    permissions: "%s"\n    encoding: b64\n    content: %s\n' "$2" "$3" "$(base64 -w0 "$1")" >> "$work/user-data"; }
add_file /usr/local/bin/gitea-runner /usr/local/bin/gitea-runner 0755
add_file "$here/guest-runner.yaml" /etc/mag-ci-runner.yaml 0644
add_file "$here/mag-ci-guest-runner.service" /etc/systemd/system/mag-ci-guest-runner.service 0644
cat >> "$work/user-data" <<'CLOUD'
runcmd:
  - [bash, -lc, 'set -euo pipefail; python3 -m venv /opt/ci-venv; /opt/ci-venv/bin/pip install playwright pyyaml; /opt/ci-venv/bin/python -m playwright install --with-deps chromium']
  - [bash, -lc, 'set -euo pipefail; GOBIN=/usr/local/bin go install github.com/zricethezav/gitleaks/v8@v8.24.0']
  - [bash, -lc, 'set -euo pipefail; curl --proto "=https" -fsSLo /tmp/restic.bz2 https://github.com/restic/restic/releases/download/v0.18.1/restic_0.18.1_linux_amd64.bz2; echo "680838f19d67151adba227e1570cdd8af12c19cf1735783ed1ba928bc41f363d  /tmp/restic.bz2" | sha256sum -c -; bunzip2 -c /tmp/restic.bz2 > /usr/local/bin/restic; chmod 755 /usr/local/bin/restic; rm /tmp/restic.bz2']
  - [bash, -lc, 'set -euo pipefail; mkdir -p /var/lib/mag-ci-runner /work; systemctl daemon-reload; test -x /usr/local/bin/gitea-runner; test -x /usr/local/bin/gitleaks; test -x /usr/local/bin/restic; for tool in g++ cmake ninja node go nginx nft podman jq sqlite3 shellcheck; do command -v "$tool"; done; /opt/ci-venv/bin/python -c "from playwright.sync_api import sync_playwright; p=sync_playwright().start(); b=p.chromium.launch(); b.close(); p.stop()"; touch /var/lib/mag-ci-image.ready']
power_state:
  delay: now
  mode: poweroff
  timeout: 60
  condition: test -f /var/lib/mag-ci-image.ready
CLOUD
printf 'instance-id: mag-ci-image-%s\nlocal-hostname: mag-ci-image\n' "$(cat /proc/sys/kernel/random/uuid)" > "$work/meta-data"
cat > "$work/network-config" <<'NET'
version: 2
ethernets:
  ci:
    match: {macaddress: '52:54:00:fe:ff:02'}
    addresses: [172.31.255.2/30]
    routes: [{to: default, via: 172.31.255.1}]
    nameservers: {addresses: [1.1.1.1, 8.8.8.8]}
NET
cloud-localds --network-config="$work/network-config" "$work/seed.iso" "$work/user-data" "$work/meta-data"
# Guest UID 0 has no shared host directory or privileged host device.
timeout --signal=TERM 3h qemu-system-x86_64 -enable-kvm -cpu host -smp 2 -m 12288 \
 -name mag-ci-image -nographic -nodefaults -no-reboot -display none -monitor none \
 -drive file="$work/base.qcow2",if=virtio,format=qcow2,cache=none \
 -drive file="$work/seed.iso",media=cdrom,format=raw,readonly=on \
 -netdev tap,id=ci,ifname=magci0,script=no,downscript=no \
 -device virtio-net-pci,netdev=ci,mac=52:54:00:fe:ff:02 -serial file:"$work/console.log"
# A clean shutdown alone isn't success: inspect the explicit provisioning stamp.
virt-cat -a "$work/base.qcow2" /var/lib/mag-ci-image.ready >/dev/null
install -m 0440 -o root -g mag-ci-vm "$work/base.qcow2" /var/lib/mag-ci/base.qcow2.next
mv -f /var/lib/mag-ci/base.qcow2.next /var/lib/mag-ci/base.qcow2
echo 'MAG CI base image ready'
