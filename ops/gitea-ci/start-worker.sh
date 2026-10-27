#!/usr/bin/env bash
set +x
set -euo pipefail
[[ $EUID == 0 && -s /var/lib/mag-ci/base.qcow2 ]] || exit 1
# A repeated wake-up must never cut off a running job (including a frozen VM).
systemctl is-active --quiet mag-ci-vm.service && exit 0
exec 9>/run/lock/mag-ci-worker.lock
flock -n 9 || exit 0
systemctl is-active --quiet mag-ci-vm.service && exit 0
work=$(mktemp -d /run/mag-ci-register.XXXXXXXX);chmod 700 "$work"
trap 'rm -rf "$work"' EXIT
printf 'Authorization: token %s\n' "$(tr -d '\r\n' < "${CREDENTIALS_DIRECTORY:?}/gitea")" > "$work/header"
code=$(curl -sS --max-time 15 -o "$work/reply" -w '%{http_code}' -X POST -H @"$work/header" \
  http://127.0.0.1:3000/api/v1/repos/mag-ci/mag-portable-stack/actions/runners/registration-token)
[[ $code == 200 ]] || { echo 'Scoped runner token unavailable' >&2; exit 1; }
jq -er '.token' "$work/reply" > "$work/token"
cat > "$work/runner.yaml" <<CFG
runner:
  file: $work/.runner
  capacity: 1
  labels: ["mag-ci-vm:host"]
CFG
# Only the revoked-on-assignment ephemeral identity enters the VM. The
# reusable registration token and controller PAT stay on the host.
(cd "$work"; /usr/local/bin/gitea-runner --config "$work/runner.yaml" register --no-interactive --ephemeral \
  --instance http://172.31.255.1:3000 --token-file "$work/token" --name mag-ci-ephemeral --labels mag-ci-vm:host >/dev/null)
[[ -s $work/.runner ]] || exit 1
install -d -m 0750 -o mag-ci-vm -g mag-ci-vm /var/lib/mag-ci/vm
rm -f /var/lib/mag-ci/vm/job.qcow2 /var/lib/mag-ci/vm/seed.iso /var/lib/mag-ci/vm/console.log
qemu-img create -q -f qcow2 -F qcow2 -b /var/lib/mag-ci/base.qcow2 /var/lib/mag-ci/vm/job.qcow2
printf 'instance-id: mag-ci-%s\nlocal-hostname: mag-ci\n' "$(cat /proc/sys/kernel/random/uuid)" > "$work/meta-data"
cat > "$work/user-data" <<'CLOUD'
#cloud-config
ssh_pwauth: false
disable_root: true
write_files:
  - path: /var/lib/mag-ci-runner/.runner
    permissions: '0600'
    encoding: b64
CLOUD
printf '    content: %s\n' "$(base64 -w0 "$work/.runner")" >> "$work/user-data"
cat >> "$work/user-data" <<'CLOUD'
runcmd:
  - [systemctl, start, mag-ci-guest-runner.service]
CLOUD
cat > "$work/network-config" <<'NET'
version: 2
ethernets:
  ci:
    match: {macaddress: '52:54:00:fe:ff:02'}
    addresses: [172.31.255.2/30]
    routes: [{to: default, via: 172.31.255.1}]
    nameservers: {addresses: [1.1.1.1, 8.8.8.8]}
NET
cloud-localds --network-config="$work/network-config" /var/lib/mag-ci/vm/seed.iso "$work/user-data" "$work/meta-data"
chown mag-ci-vm:mag-ci-vm /var/lib/mag-ci/vm/job.qcow2 /var/lib/mag-ci/vm/seed.iso
chmod 600 /var/lib/mag-ci/vm/job.qcow2 /var/lib/mag-ci/vm/seed.iso
systemctl start mag-ci-workload.slice mag-ci-guard.service
systemctl start mag-ci-vm.service
