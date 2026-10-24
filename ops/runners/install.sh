#!/usr/bin/env bash
# Action runners on the MAG host (idempotent, run as root; ops/update.sh and the
# auto-update call it after each successful deploy).
#  - Gitea Actions only for the bot orgs bots-<agent>: one org-scoped,
#    sandboxed runner per org (bot-runners.sh, runs-on: mag-bot), no deploy
#    rights. Optional host runner (runs-on: mag, deploy rights) only for the
#    owner named in /etc/mag-stack/host-runner.owner, at that owner's scope.
#  - GitHub runner (runs-on: [self-hosted, mag]) only when the owner has put a
#    registration token into /etc/credstore/mag-github-runner.token.
# Jobs run as unprivileged users with CPU/RAM caps; they never see the models,
# room state, credentials or backups. A host/GitHub runner job deploys by writing to
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
  install -d -m 0750 -o "$1" -g "$1" "$2" "$2/work"
}

# Deploy trigger shared by both runners.
getent group mag-deploy >/dev/null || groupadd --system mag-deploy
install -d -m 0750 -o root -g mag-deploy /var/lib/mag-deploy
[[ -f /var/lib/mag-deploy/request ]] || install -m 0660 -o root -g mag-deploy /dev/null /var/lib/mag-deploy/request
chgrp mag-deploy /var/lib/mag-deploy/request; chmod 0660 /var/lib/mag-deploy/request
install -m 0644 "$here/mag-deploy-request.path" /etc/systemd/system/mag-deploy-request.path
restart_gitea=0


# Gitea: Actions on; runners only at org scope for the bot orgs (bot-runners.sh)
# plus, opt-in, one owner-scoped host runner. Never an instance-wide runner.
if [[ -x /usr/local/bin/gitea && -r /etc/gitea/app.ini ]] && systemctl is-active --quiet gitea.service; then
  bash "$here/../gitea/enable-actions.sh" || echo 'WARN: Gitea Actions could not be enabled' >&2
  restart_bot=0
  if ! /usr/local/bin/gitea-runner --version 2>/dev/null | grep -Fq "$GITEA_RUNNER_VER"; then
    tmp="$(mktemp -d /var/tmp/mag-runner.XXXXXXXX)"
    fetch "https://gitea.com/gitea/runner/releases/download/v${GITEA_RUNNER_VER}/gitea-runner-${GITEA_RUNNER_VER}-linux-amd64.xz" "$GITEA_RUNNER_SHA" "$tmp/r.xz"
    xz -dc "$tmp/r.xz" > "$tmp/gitea-runner"
    install -m 0755 -o root -g root "$tmp/gitea-runner" /usr/local/bin/.gitea-runner-next
    mv -f /usr/local/bin/.gitea-runner-next /usr/local/bin/gitea-runner
    restart_gitea=1; restart_bot=1
    rm -rf -- "$tmp"
  fi
  put() { # src dst mode: install when different, then flag a runner restart
    cmp -s "$1" "$2" || { install -m "$3" -o root -g root "$1" "$2"; restart_bot=1; }
  }
  getent group mag-botrunner >/dev/null || groupadd --system mag-botrunner
  port="$(sed -n 's/^HTTP_PORT *= *//p' /etc/gitea/app.ini | head -1)"
  install -d -m 0755 /etc/mag-botrunner
  sed -e "s/@GID@/$(getent group mag-botrunner | cut -d: -f3)/" -e "s/@PORT@/${port:-3000}/" \
    "$here/botrunner-egress.nft" > /run/mag-botrunner-egress.nft
  put /run/mag-botrunner-egress.nft /etc/mag-botrunner/egress.nft 0644; rm -f /run/mag-botrunner-egress.nft
  put "$here/botrunner.yaml" /etc/mag-botrunner/config.yaml 0644
  put "$here/mag-botrunner.slice" /etc/systemd/system/mag-botrunner.slice 0644
  put "$here/mag-gitea-botrunner@.service" /etc/systemd/system/mag-gitea-botrunner@.service 0644
  install -m 0755 "$here/bot-runners.sh" /usr/local/sbin/mag-gitea-bot-runners
  install -m 0755 "$here/status.sh" /usr/local/sbin/mag-gitea-runner-status
  systemctl daemon-reload
  [[ -x /usr/sbin/nft ]] || echo 'WARN: nft missing; bot runners cannot start (egress filter fails closed)' >&2
  MAG_BOTRUNNER_RESTART=$restart_bot /usr/local/sbin/mag-gitea-bot-runners || true  # prints its own WARN lines

  # Host runner with deploy rights: only when the owner opts in with
  # /etc/mag-stack/host-runner.owner = <Gitea user>, registered at THAT
  # owner's scope (never instance-wide, never a bot owner).
  owner="$(tr -d ' \n' < /etc/mag-stack/host-runner.owner 2>/dev/null || true)"
  if [[ -n $owner && $owner =~ ^[A-Za-z0-9._-]+$ && ! $owner =~ ^(bots-|magbot-|magrun-) ]]; then
    ensure_user mag-runner /var/lib/mag-runner
    usermod -aG mag-deploy mag-runner
    install -d -m 0755 /etc/mag-runner
    cmp -s "$here/gitea-runner.yaml" /etc/mag-runner/config.yaml || { install -m 0644 "$here/gitea-runner.yaml" /etc/mag-runner/config.yaml; restart_gitea=1; }
    [[ "$(cat /var/lib/mag-runner/.mag-scope 2>/dev/null)" == "$owner" ]] || rm -f /var/lib/mag-runner/.runner
    if [[ ! -s /var/lib/mag-runner/.runner ]]; then
      addr="$(sed -n 's/^HTTP_ADDR *= *//p' /etc/gitea/app.ini | head -1)"
      tdir="$(mktemp -d /run/mag-runner.XXXXXXXX)"
      if runuser -u git -- /usr/local/bin/gitea --config /etc/gitea/app.ini actions generate-runner-token --scope "$owner" > "$tdir/t" 2>/dev/null && [[ -s $tdir/t ]] &&
         (cd /var/lib/mag-runner && runuser -u mag-runner -- /usr/local/bin/gitea-runner --config /etc/mag-runner/config.yaml \
           register --no-interactive --instance "http://${addr:-127.0.0.1}:${port:-3000}" --token-file /dev/stdin --name mag-host < "$tdir/t" >/dev/null 2>&1); then
        echo "$owner" > /var/lib/mag-runner/.mag-scope
        echo "OK: Gitea host runner (scope $owner, label mag)"
      else
        echo "WARN: Gitea host runner registration for $owner failed" >&2
      fi
      rm -rf -- "$tdir"
    fi
    if [[ -s /var/lib/mag-runner/.runner ]]; then
      cmp -s "$here/mag-gitea-runner.service" /etc/systemd/system/mag-gitea-runner.service ||
        { install -m 0644 "$here/mag-gitea-runner.service" /etc/systemd/system/mag-gitea-runner.service; restart_gitea=1; }
    fi
  else
    [[ -z $owner ]] || echo "WARN: /etc/mag-stack/host-runner.owner '$owner' refused (bots never get the host runner)" >&2
    if [[ -e /etc/systemd/system/mag-gitea-runner.service ]]; then
      systemctl disable --now mag-gitea-runner.service >/dev/null 2>&1 || true
      rm -f /etc/systemd/system/mag-gitea-runner.service
    fi
    rm -f /var/lib/mag-runner/.runner /var/lib/mag-runner/.mag-scope
    if id mag-runner >/dev/null 2>&1 && [[ " $(id -nG mag-runner) " == *" mag-deploy "* ]]; then gpasswd -d mag-runner mag-deploy >/dev/null; fi
  fi
else
  echo 'WARN: Gitea not installed/active; Gitea runners skipped' >&2
fi

# GitHub runner (optional, needs a registration token from the owner)
GH=/var/lib/mag-ghrunner
if [[ -s /etc/credstore/mag-github-runner.token || -s $GH/runner/.runner ]]; then
  ensure_user mag-ghrunner "$GH"
  usermod -aG mag-deploy mag-ghrunner
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
