#!/usr/bin/env bash
# Action runners on the MAG host (idempotent, run as root; ops/update.sh and the
# auto-update call it after each successful deploy).
#  - Gitea runner (GitHub-style workflows in .gitea/workflows, runs-on: mag),
#    registered automatically against the local Gitea.
#  - GitHub runner (runs-on: [self-hosted, mag]) only when the owner has put a
#    registration token into /etc/credstore/mag-github-runner.token.
# Jobs run as unprivileged users with CPU/RAM caps; they never see the models,
# room state, credentials or backups. A job deploys by writing to
# /var/lib/mag-deploy/request, which starts the normal auto-update
# (fast-forward main, build, deploy.sh with backup and automatic rollback).
# Disable: sudo touch /etc/mag-stack/runners.disabled
set -euo pipefail
[[ $EUID == 0 ]] || { echo 'root required' >&2; exit 1; }
here="$(cd -- "$(dirname "$0")" && pwd)"
log() { echo "runners: $*"; }
[[ ! -e /etc/mag-stack/runners.disabled ]] || { log 'disabled'; exit 0; }

GITEA_RUNNER_VER=5.1.1
GITEA_RUNNER_SHA=07b7304cafac138152a9d34790c100af9493278ef63f097de47fa16071d768a1
GH_RUNNER_VER=2.338.0
GH_RUNNER_SHA=af4b794c1bc41d73d40535e3fe092a39f9679cd8d965954c2aca25a05ca41d32
GH_REPO_URL=https://github.com/ChristianHohlfeld/mag-portable-stack

fetch() { # url sha out
  curl --proto '=https' --tlsv1.2 -fsSL --retry 3 --max-time 300 "$1" -o "$3"
  printf '%s  %s\n' "$2" "$3" | sha256sum -c --quiet -
}
ensure_user() { # name home
  id "$1" >/dev/null 2>&1 || useradd --system --create-home --home-dir "$2" --shell /usr/sbin/nologin "$1"
  usermod -aG mag-deploy "$1"
  install -d -m 0750 -o "$1" -g "$1" "$2" "$2/work"
}

# Deploy trigger shared by both runners.
getent group mag-deploy >/dev/null || groupadd --system mag-deploy
install -d -m 0750 -o root -g mag-deploy /var/lib/mag-deploy
[[ -f /var/lib/mag-deploy/request ]] || install -m 0660 -o root -g mag-deploy /dev/null /var/lib/mag-deploy/request
chgrp mag-deploy /var/lib/mag-deploy/request; chmod 0660 /var/lib/mag-deploy/request
install -m 0644 "$here/mag-deploy-request.path" /etc/systemd/system/mag-deploy-request.path
restart_gitea=0


# Gitea runner
if [[ -x /usr/local/bin/gitea && -r /etc/gitea/app.ini ]] && systemctl is-active --quiet gitea.service; then
  if ! /usr/local/bin/gitea-runner --version 2>/dev/null | grep -Fq "$GITEA_RUNNER_VER"; then
    tmp="$(mktemp -d /var/tmp/mag-runner.XXXXXXXX)"
    fetch "https://gitea.com/gitea/runner/releases/download/v${GITEA_RUNNER_VER}/gitea-runner-${GITEA_RUNNER_VER}-linux-amd64.xz" "$GITEA_RUNNER_SHA" "$tmp/r.xz"
    xz -dc "$tmp/r.xz" > "$tmp/gitea-runner"
    install -m 0755 -o root -g root "$tmp/gitea-runner" /usr/local/bin/.gitea-runner-next
    mv -f /usr/local/bin/.gitea-runner-next /usr/local/bin/gitea-runner
    restart_gitea=1
    rm -rf -- "$tmp"
  fi
  ensure_user mag-runner /var/lib/mag-runner
  install -d -m 0755 /etc/mag-runner
  cmp -s "$here/gitea-runner.yaml" /etc/mag-runner/config.yaml || { install -m 0644 "$here/gitea-runner.yaml" /etc/mag-runner/config.yaml; restart_gitea=1; }
  if [[ ! -s /var/lib/mag-runner/.runner ]]; then
    addr="$(sed -n 's/^HTTP_ADDR *= *//p' /etc/gitea/app.ini | head -1)"; port="$(sed -n 's/^HTTP_PORT *= *//p' /etc/gitea/app.ini | head -1)"
    instance="http://${addr:-127.0.0.1}:${port:-3000}"
    tok="$(install -m 0600 -o mag-runner -g mag-runner /dev/null /var/lib/mag-runner/.token && echo /var/lib/mag-runner/.token)"
    if runuser -u git -- /usr/local/bin/gitea --config /etc/gitea/app.ini actions generate-runner-token > "$tok" 2>/dev/null && [[ -s "$tok" ]]; then
      (cd /var/lib/mag-runner && runuser -u mag-runner -- /usr/local/bin/gitea-runner --config /etc/mag-runner/config.yaml \
        register --no-interactive --instance "$instance" --token-file "$tok" --name mag-host --labels 'mag:host,self-hosted:host') \
        || log 'Gitea runner registration failed'
    else
      log 'Gitea Actions token not available (actions disabled in app.ini?); Gitea runner skipped'
    fi
    rm -f "$tok"
  fi
  if [[ -s /var/lib/mag-runner/.runner ]]; then
    cmp -s "$here/mag-gitea-runner.service" /etc/systemd/system/mag-gitea-runner.service ||
      { install -m 0644 "$here/mag-gitea-runner.service" /etc/systemd/system/mag-gitea-runner.service; restart_gitea=1; }
  fi
else
  log 'Gitea not installed/active; Gitea runner skipped'
fi

# GitHub runner (optional, needs a registration token from the owner)
GH=/var/lib/mag-ghrunner
if [[ -s /etc/credstore/mag-github-runner.token || -s $GH/runner/.runner ]]; then
  ensure_user mag-ghrunner "$GH"
  if [[ ! -x $GH/runner/config.sh ]] || ! grep -Fq "$GH_RUNNER_VER" "$GH/runner/.version" 2>/dev/null; then
    tmp="$(mktemp -d /var/tmp/mag-ghrunner.XXXXXXXX)"
    fetch "https://github.com/actions/runner/releases/download/v${GH_RUNNER_VER}/actions-runner-linux-x64-${GH_RUNNER_VER}.tar.gz" "$GH_RUNNER_SHA" "$tmp/r.tgz"
    install -d -m 0750 -o mag-ghrunner -g mag-ghrunner "$GH/runner"
    tar -xzf "$tmp/r.tgz" -C "$GH/runner"
    echo "$GH_RUNNER_VER" > "$GH/runner/.version"
    chown -R mag-ghrunner:mag-ghrunner "$GH/runner"
    rm -rf -- "$tmp"
    "$GH/runner/bin/installdependencies.sh" >/dev/null || log 'GitHub runner dependency install reported errors'
  fi
  if [[ ! -s $GH/runner/.runner ]]; then
    (cd "$GH/runner" && runuser -u mag-ghrunner -- ./config.sh --unattended --url "$GH_REPO_URL" \
      --token "$(cat /etc/credstore/mag-github-runner.token)" --name mag-host --labels mag \
      --work "$GH/work" --disableupdate --replace) || log 'GitHub runner registration failed (token expired? tokens live 1 hour)'
    rm -f /etc/credstore/mag-github-runner.token
  fi
  if [[ -s $GH/runner/.runner ]]; then
    install -m 0644 "$here/mag-github-runner.service" /etc/systemd/system/mag-github-runner.service
  fi
fi

systemctl daemon-reload
systemctl enable --now mag-deploy-request.path
if [[ -e /etc/systemd/system/mag-gitea-runner.service ]]; then
  systemctl enable --now mag-gitea-runner.service
  # Restart only on a new binary/config/unit so a running job is not cut off.
  if (( restart_gitea )); then systemctl restart mag-gitea-runner.service; fi
fi
if [[ -e /etc/systemd/system/mag-github-runner.service ]]; then
  systemctl enable --now mag-github-runner.service
fi
log done
