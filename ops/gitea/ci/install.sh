#!/usr/bin/env bash
# Install from the reviewed, deployed checkout, never from a job worktree.
set -euo pipefail
[[ $EUID == 0 ]] || exit 1
here=$(cd -- "$(dirname -- "$0")" && pwd)
for f in github-ci.token gitea-ci.token; do
 [[ -s /etc/credstore/$f && ! -L /etc/credstore/$f && $(stat -c '%u:%a' "/etc/credstore/$f") =~ ^0:(400|600)$ ]] || { echo "Missing root-only credential: $f" >&2; exit 1; }
done
for user in mag-ci mag-ci-bridge; do
 id "$user" >/dev/null 2>&1 || useradd --system --create-home --home-dir "/var/lib/$user" --shell /usr/sbin/nologin "$user"
 # Never inherit the existing host runner's deploy permission.
 if id -nG "$user" | tr ' ' '\n' | grep -qx mag-deploy; then gpasswd -d "$user" mag-deploy >/dev/null; fi
done
install -d -m 0755 /usr/local/lib/mag-ci /etc/mag-ci
install -d -m 0700 -o mag-ci -g mag-ci /var/lib/mag-ci /var/lib/mag-ci/work
install -m 0755 "$here/bridge.sh" /usr/local/lib/mag-ci/bridge.sh
install -m 0644 "$here/runner.yaml" /etc/mag-ci/runner.yaml
for f in mag-ci.slice mag-ci-runner.service mag-ci-bridge.service mag-ci-bridge.timer; do
 install -m 0644 "$here/$f" "/etc/systemd/system/$f"
done
# Reuse the repository's pinned runner version (formerly act_runner).
if ! /usr/local/bin/gitea-runner --version 2>/dev/null | grep -Fq 5.1.1; then
 tmp=$(mktemp -d); trap 'rm -rf -- "$tmp"' EXIT
 curl --proto '=https' --tlsv1.2 -fsSL --retry 3 --max-time 300 https://gitea.com/gitea/runner/releases/download/v5.1.1/gitea-runner-5.1.1-linux-amd64.xz -o "$tmp/runner.xz"
 printf '%s  %s\n' 07b7304cafac138152a9d34790c100af9493278ef63f097de47fa16071d768a1 "$tmp/runner.xz" | sha256sum -c --quiet -
 xz -dc "$tmp/runner.xz" > "$tmp/runner"
 install -m 0755 "$tmp/runner" /usr/local/bin/gitea-runner
 rm -rf -- "$tmp"; trap - EXIT
fi
# The CI mirror is a separate normal repository: git pushes trigger Actions.
# Existing pull mirrors and bot-owned repos/gateway are never modified.
tmp=$(mktemp -d); trap 'rm -rf -- "$tmp"' EXIT
printf 'Authorization: token %s\n' "$(tr -d '\r\n' < /etc/credstore/gitea-ci.token)" > "$tmp/auth"
code=$(curl -sS -o "$tmp/out" -w '%{http_code}' -H @"$tmp/auth" http://127.0.0.1:3000/api/v1/repos/chris/mag-portable-stack-ci)
if [[ $code == 404 ]]; then
 printf '%s\n' '{"name":"mag-portable-stack-ci","private":true,"auto_init":false}' > "$tmp/body"
 curl -fsS -o /dev/null -H @"$tmp/auth" -H 'Content-Type: application/json' --data-binary @"$tmp/body" http://127.0.0.1:3000/api/v1/user/repos
elif [[ $code != 200 ]]; then echo "Gitea API refused setup ($code)" >&2; exit 1
elif [[ $(jq -r .mirror "$tmp/out") == true ]]; then echo 'Refusing to replace an existing pull mirror' >&2; exit 1
fi
printf '%s\n' '{"has_actions":true}' > "$tmp/body"
curl -fsS -o /dev/null -X PATCH -H @"$tmp/auth" -H 'Content-Type: application/json' --data-binary @"$tmp/body" http://127.0.0.1:3000/api/v1/repos/chris/mag-portable-stack-ci
if [[ ! -s /var/lib/mag-ci/.runner ]]; then
 runuser -u git -- /usr/local/bin/gitea --config /etc/gitea/app.ini actions generate-runner-token --scope chris/mag-portable-stack-ci > "$tmp/token"
 (cd /var/lib/mag-ci && runuser -u mag-ci -- /usr/local/bin/gitea-runner --config /etc/mag-ci/runner.yaml register --no-interactive --instance http://127.0.0.1:3000 --token-file /dev/stdin --name mag-stack-ci --labels mag-ci:host < "$tmp/token" >/dev/null 2>&1)
fi
systemctl daemon-reload
systemctl enable --now mag-ci-runner.service mag-ci-bridge.timer
