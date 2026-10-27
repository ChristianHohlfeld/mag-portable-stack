#!/usr/bin/env bash
# Transactional public Gitea cutover + isolated CI bootstrap, called by update.
# Missing the scoped GitHub credential fails closed and never advertises CI success.
# Public HTTPS is Tailscale Funnel. There is no public DNS or port forward.
set +x
set -euo pipefail
[[ $EUID == 0 ]] || { echo 'root required' >&2;exit 1; }
root=$(cd -- "$(dirname -- "$0")/../.." && pwd)
if [[ ${1:-} == --dry-run ]]; then
 echo '+ Gitea on Tailscale Funnel :443; mandatory human 2FA; preserve VM Git accounts'
 echo '+ one-repo mirror/status bridge, ephemeral isolated runner, idle guard'
 echo '+ requires /etc/credstore/mag-ci-github.token'
 exit 0
fi
exec 9>/run/lock/mag-gitea-ci-install.lock;flock -n 9 || exit 0
install -d -m 0700 /etc/credstore /etc/mag-ci /etc/mag-ci/tls
install -d -m 0755 /usr/local/lib/mag-ci
install -m 0644 "$root"/ops/gitea-ci/*.{json,hpp,yaml,service,socket,slice,path,timer,inc,in} /usr/local/lib/mag-ci/
install -m 0755 "$root"/ops/gitea-ci/*.sh /usr/local/lib/mag-ci/
rm -f /usr/local/lib/mag-ci/http.nginx.in
install -m 0644 "$root/ops/gitea-ci/token-permissions.sql" /usr/local/lib/mag-ci/
install -m 0755 "$root/ops/gitea-ci/bot-auth.sh" /usr/local/sbin/mag-gitea-bot-auth
install -m 0755 "$root/ops/gitea/bot-rights.sh" /usr/local/sbin/mag-gitea-bot-rights
# No automatic credential collection, account scraping, or broader GitHub PAT.
# Public reachability is Tailscale Funnel. No public DNS, port forward, or lego.
for s in mag-ci-github.token; do
 [[ -s /etc/credstore/$s && ! -L /etc/credstore/$s && $(stat -c '%u:%a' /etc/credstore/$s) =~ ^0:(400|600)$ ]] || { echo "Credential pending: /etc/credstore/$s (root 0600)" >&2;exit 42; }
done
apt-get update
apt-get install -y qemu-system-x86 qemu-utils cloud-image-utils libguestfs-tools ubuntu-cloudimage-keyring \
  nginx jq curl xz-utils nftables iproute2 dnsutils openssl cmake ninja-build libssl-dev libcurl4-openssl-dev libicu-dev
bash "$root/ops/gitea/install.sh"
bash "$root/ops/gitea-ci/fetch-runner.sh"
# Install supporting helpers before the VM credential upgrade.
for u in mag-ci-control mag-ci-vm; do
 id "$u" >/dev/null 2>&1 || useradd --system --home-dir "/var/lib/mag-ci/$u" --shell /usr/sbin/nologin "$u"
done
usermod -aG kvm mag-ci-vm
install -d -m 0755 -o root -g root /var/lib/mag-ci
install -d -m 0700 -o mag-ci-control -g mag-ci-control /var/lib/mag-ci/control
install -m 0600 -o mag-ci-control -g mag-ci-control /dev/null /var/lib/mag-ci/request
install -d -m 0755 /var/lib/mag-ci/acme
avail=$(df -Pk /var/lib/mag-ci | awk 'NR==2 {print $4}')
(( avail >= 100*1024*1024 )) || { echo 'CI requires 100 GiB disk reserve (64 GiB bounded overlay plus host reserve)' >&2;exit 1; }
test -x "$root/build/mag-ci-bridge" && test -x "$root/build/mag-ci-guard"
bridge_changed=0
cmp -s "$root/build/mag-ci-bridge" /usr/local/lib/mag-ci/mag-ci-bridge || bridge_changed=1
for bin in mag-ci-bridge mag-ci-guard;do
 install -m 0755 "$root/build/$bin" "/usr/local/lib/mag-ci/.$bin.next"
 mv -f "/usr/local/lib/mag-ci/.$bin.next" "/usr/local/lib/mag-ci/$bin"
done
install -m 0600 "$root/ops/gitea-ci/policy.json" /etc/mag-ci/policy.json
chown root:mag-ci-control /etc/mag-ci/policy.json;chmod 640 /etc/mag-ci/policy.json
# /etc/mag-ci itself must permit the bridge to traverse, without exposing keys.
chown root:mag-ci-control /etc/mag-ci;chmod 750 /etc/mag-ci
if [[ ! -s /etc/credstore/mag-ci-webhook.secret ]]; then openssl rand -hex 32 > /etc/credstore/mag-ci-webhook.secret;chmod 600 /etc/credstore/mag-ci-webhook.secret;fi
bash /usr/local/lib/mag-ci/setup-accounts.sh
# Upgrade ready, owned VMs in place. Stopped VMs upgrade during next git-sync/
# spawn before executing work; no VM, model, or browser is stopped/recreated.
vmctl=/opt/mag-stack/current/bin/magbot-native
[[ -x $vmctl ]] || vmctl="$root/build/magbot-native"
[[ -x $vmctl ]] || exit 1
mapfile -t ready < <("$vmctl" status | awk '$2=="ready" {print $1}')
for vm in "${ready[@]}";do "$vmctl" git-auth-sync "$vm";done
install -d -m 0700 /var/lib/mag-ci/rollback
cp -a /etc/gitea/app.ini /var/lib/mag-ci/rollback/app.ini
site=/etc/nginx/conf.d/mag-git.conf
rm -f /var/lib/mag-ci/rollback/nginx.conf
[[ ! -e $site ]] || cp -a "$site" /var/lib/mag-ci/rollback/nginx.conf
changed_gitea=0
restore() {
 cp -a /var/lib/mag-ci/rollback/app.ini /etc/gitea/app.ini
 if [[ -s /var/lib/mag-ci/rollback/nginx.conf ]];then cp -a /var/lib/mag-ci/rollback/nginx.conf "$site";else rm -f "$site";fi
 if (( changed_gitea ));then systemctl restart gitea.service;fi
 nginx -t && systemctl reload nginx.service
}
trap 'rc=$?; trap - ERR; restore || true; exit "$rc"' ERR
permissions_changed=$(bash /usr/local/lib/mag-ci/token-permissions.sh)
[[ $permissions_changed == 0 || $permissions_changed == 1 ]] || exit 1
bash /usr/local/lib/mag-ci/configure-gitea.sh
if [[ $permissions_changed == 1 ]] || ! cmp -s /etc/gitea/app.ini /var/lib/mag-ci/rollback/app.ini;then
 changed_gitea=1;systemctl restart gitea.service
fi
for n in $(seq 1 60);do curl -fsS --max-time 1 http://127.0.0.1:3000/api/healthz >/dev/null && break;sleep 1;done
curl -fsS --max-time 2 http://127.0.0.1:3000/api/healthz >/dev/null
bash "$root/ops/tailscale/apply-gitea-funnel.sh"
for n in $(seq 1 60);do curl -fsS --max-time 1 http://127.0.0.1:3000/api/healthz >/dev/null && break;sleep 1;done
curl -fsS --max-time 2 http://127.0.0.1:3000/api/healthz >/dev/null
curl -fsS --max-time 5 --haproxy-protocol \
  --resolve mag.tail3e02ae.ts.net:4443:127.0.0.1 \
  https://mag.tail3e02ae.ts.net:4443/user/login >/dev/null
bash /usr/local/lib/mag-ci/install-control.sh "$root"
bash /usr/local/lib/mag-ci/setup-webhook.sh
for unit in "$root"/ops/gitea-ci/*.{service,socket,slice,path,timer};do
 base=$(basename "$unit")
 [[ $base == mag-ci-guest-runner.service || $base == mag-ci-cert.service || $base == mag-ci-cert.timer ]] && continue
 install -m 0644 "$unit" /etc/systemd/system/
done
# The host Gitea runner is the merge gate: it may write the deploy request.
# The GitHub runner is not part of promotion or deploy.
systemctl disable --now mag-github-runner.service 2>/dev/null || true
systemctl daemon-reload
systemctl disable --now mag-ci-cert.timer mag-ci-cert.service >/dev/null 2>&1 || true
systemctl enable --now mag-ci-network.service mag-ci-git.socket
systemctl start mag-ci-workload.slice
systemctl enable --now mag-ci-guard.service mag-ci-launch.path mag-ci-bridge.service
if (( bridge_changed ));then systemctl try-restart mag-ci-bridge.service;fi
# First image preparation is asynchronous, subject to inference idle gating.
systemctl enable mag-ci-image.service
systemctl start --no-block mag-ci-image.service
touch /etc/mag-stack/gitea-ci.enabled
trap - ERR
echo 'Public Git + isolated Gitea CI installed; live acceptance still required'
