#!/usr/bin/env bash
# Pages-only activation AFTER other MAG updates. NO ops/deploy.sh or backup.
set -euo pipefail
[[ $EUID == 0 ]] || { echo 'Run: sudo bash ops/pages/activate.sh' >&2; exit 1; }
here="$(cd "$(dirname "$0")" && pwd)"
config=/etc/mag-pages/pages.env
[[ -f "$config" && ! -L "$config" && -r "$config" ]] ||
  { echo "Missing root-owned $config; configure pages.env.example" >&2; exit 1; }
. "$config"
[[ "$PAGES_ZONE" == pages.stardenburg.de &&
   "${PAGES_TAILNET_BIND:-}" == 100.123.114.16 &&
   -z "${PAGES_PUBLIC_BIND:-}" && -z "${PAGES_PUBLIC_DNS:-}" ]] ||
  { echo 'Not a Tailnet-only Pages configuration; refusing activation' >&2; exit 1; }
[[ -f /etc/mag-acme/lego.env && ! -L /etc/mag-acme/lego.env ]] ||
  { echo 'Missing shared root-only lego.env (acme-dns)' >&2; exit 1; }
[[ -s /etc/mag-pages/gitea-token && ! -L /etc/mag-pages/gitea-token ]] ||
  { echo 'Existing Pages read:repository token missing; do not make a new one' >&2; exit 1; }
for bin in /usr/local/bin/lego jq dig strings curl nginx systemctl openssl ip runuser; do
  command -v "$bin" >/dev/null ||
    { echo "Missing binary: $bin" >&2; exit 1; }
done
runuser -u magbot -- test -r /etc/mag-pages/gitea-token ||
  { echo 'magbot cannot read existing Pages token' >&2; exit 1; }
systemctl is-enabled --quiet mag-acme-renew.timer ||
  { echo 'Existing MAG ACME timer must be enabled; DO NOT enable backup timers' >&2; exit 1; }
# Never turn Pages on against an older deployed mag-120 with no matching ABI.
binary=/opt/mag-stack/current/bin/magroom
[[ -x "$binary" ]] || { echo 'Native MAG room binary missing' >&2; exit 1; }
strings -a "$binary" | grep -F 'MAG Pages: native C++ polling enabled' >/dev/null ||
  { echo 'Deployed room binary lacks native Pages publisher; deploy current binary first' >&2; exit 1; }
strings -a "$binary" | grep -F 'pages_set_visibility' >/dev/null ||
  { echo 'Deployed room binary lacks Pages tool ABI; deploy current binary first' >&2; exit 1; }
ip -4 -o addr show dev tailscale0 | grep -F ' 100.123.114.16/' >/dev/null ||
  { echo 'Expected Tailnet IP missing from tailscale0' >&2; exit 1; }
echo 'Pages preflight OK; validating / issuing wildcard + apex with lego v5 DNS-01.'
# Registered acme-dns fulldomain MUST match live DNS. Exits before nginx
# modification on a mismatch; no secrets exposed or ACME accounts replaced.
bash "$here/issue-wildcard.sh" run
echo 'Pages TLS OK; installing root-owned portal and private nginx config.'
bash "$here/install.sh"
install -d -m 0755 /etc/systemd/system/mag-120.service.d
install -m 0644 -o root -g root "$here/mag-pages-room.conf" \
  /etc/systemd/system/mag-120.service.d/30-pages.conf
systemctl daemon-reload
# This is the ONLY application restart. No mag-llm, restic, AWS, backup timer.
systemctl restart mag-120.service
systemctl is-active --quiet mag-120.service ||
  { echo 'mag-120 failed; inspect journal and run ops/pages/rollback.sh' >&2; exit 1; }
bash "$here/verify.sh"
echo 'PASS: https://pages.stardenburg.de/ is active on Tailnet 100.123.114.16:443.'
