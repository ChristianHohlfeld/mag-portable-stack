#!/usr/bin/env bash
# The Gitea Actions runner on the MAG host (idempotent, run as root): GitHub-
# style workflows in .gitea/workflows (runs-on: [self-hosted, mag]), registered
# against the local Gitea on loopback. Jobs run as the unprivileged user
# mag-runner with CPU/RAM caps; they never see the models, room state,
# credentials or backups. Releases are deployed on purpose (ops/deploy.sh),
# never by a runner. Disable: sudo touch /etc/mag-stack/gitea-runner.disabled
set -euo pipefail
[[ $EUID == 0 ]] || { echo 'root required' >&2; exit 1; }
here="$(cd -- "$(dirname "$0")" && pwd)"
# shellcheck source=gitea-local.sh
source "$here/gitea-local.sh"
log() { echo "runners: $*"; }
GITEA_RUNNER_VER=5.1.1
GITEA_RUNNER_SHA=07b7304cafac138152a9d34790c100af9493278ef63f097de47fa16071d768a1

fetch() { # url sha out
  curl --proto '=https' --tlsv1.2 -fsSL --retry 3 --max-time 300 "$1" -o "$3"
  printf '%s  %s\n' "$2" "$3" | sha256sum -c --quiet -
}
ensure_user() { # name home
  id "$1" >/dev/null 2>&1 || useradd --system --create-home --home-dir "$2" --shell /usr/sbin/nologin "$1"
  install -d -m 0750 -o "$1" -g "$1" "$2" "$2/work"
}

restart_gitea=0

# Gitea runner. Register only against loopback after the Actions connect
# path answers. A public Funnel URL 404s that path.
if [[ -e /etc/mag-stack/gitea-runner.disabled ]]; then
  log 'Gitea runner disabled'
elif [[ -x /usr/local/bin/gitea && -r /etc/gitea/app.ini ]] && systemctl is-active --quiet gitea.service; then
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
  # Loopback only. Do not derive this from HTTP_ADDR or ROOT_URL.
  [[ $MAG_GITEA_RUNNER_INSTANCE == http://127.0.0.1:3000 ]] || { log 'refusing non-local Gitea runner instance'; exit 1; }
  actions_state=$(gitea_ensure_actions_ini /etc/gitea/app.ini)
  if [[ $actions_state == changed ]]; then
    log 'Actions enabled for loopback; restarting gitea.service'
    systemctl restart gitea.service
    gitea_wait_health || log 'gitea healthz timed out on 127.0.0.1:3000'
  fi
  ping=$(gitea_wait_actions || true)
  if ! gitea_actions_ping_ok "$ping"; then
    ver=$(gitea_binary_version || true)
    if ! gitea_actions_rpc_supported "${ver:-0}"; then
      log "Gitea ${ver:-unknown} is too old for runner ${GITEA_RUNNER_VER}; installing the pinned binary and restarting gitea.service"
      bash "$here/../gitea/install.sh" --restart
      gitea_wait_health || log 'gitea healthz timed out after the pinned install'
      ping=$(gitea_wait_actions || true)
    elif [[ $actions_state != changed ]]; then
      # The file already asks for Actions, but this process may have started before that.
      # One reload per Gitea binary; a still-404 after that is not retried every deploy.
      stamp=/etc/mag-runner/actions-reload.stamp
      if [[ ! -f $stamp || $(cat "$stamp") != "${ver:-}" ]]; then
        log 'actions connect path 404 on loopback; restarting gitea.service once'
        systemctl restart gitea.service
        printf '%s\n' "${ver:-}" > "$stamp"
        gitea_wait_health || log 'gitea healthz timed out on 127.0.0.1:3000'
        ping=$(gitea_wait_actions || true)
      fi
    fi
  fi
  if ! gitea_actions_ping_ok "$ping"; then
    log "Gitea runner skipped: ${MAG_GITEA_RUNNER_INSTANCE}/api/actions returned ${ping}"
  elif gitea_runner_needs_register /var/lib/mag-runner/.runner; then
    if [[ -s /var/lib/mag-runner/.runner ]]; then
      systemctl stop mag-gitea-runner.service || true
      rm -f /var/lib/mag-runner/.runner
      restart_gitea=1
    fi
    tok="$(install -m 0600 -o mag-runner -g mag-runner /dev/null /var/lib/mag-runner/.token && echo /var/lib/mag-runner/.token)"
    if runuser -u git -- /usr/local/bin/gitea --config /etc/gitea/app.ini actions generate-runner-token > "$tok" 2>/dev/null && [[ -s "$tok" ]]; then
      log "register gitea runner at ${MAG_GITEA_RUNNER_INSTANCE}"
      register_cmd=(
        env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY -u all_proxy
        no_proxy=127.0.0.1,localhost,::1 NO_PROXY=127.0.0.1,localhost,::1
        nice -n 19 ionice -c 3
      )
      if taskset -c 42-55 true >/dev/null 2>&1; then
        register_cmd+=(taskset -c 42-55)
      fi
      register_cmd+=(
        timeout --kill-after=5s 25s
        runuser -u mag-runner -- /usr/local/bin/gitea-runner --config /etc/mag-runner/config.yaml
        register --no-interactive --instance "$MAG_GITEA_RUNNER_INSTANCE"
        --token-file "$tok" --name mag-host --labels 'mag:host,self-hosted:host'
      )
      (cd /var/lib/mag-runner && "${register_cmd[@]}") || log 'Gitea runner registration failed'
    else
      log 'Gitea Actions token not available; Gitea runner skipped'
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

systemctl daemon-reload
if [[ ! -e /etc/mag-stack/gitea-runner.disabled && -e /etc/systemd/system/mag-gitea-runner.service ]]; then
  systemctl enable --now mag-gitea-runner.service
  # Restart only on a new binary/config/unit so a running job is not cut off.
  if (( restart_gitea )); then systemctl restart mag-gitea-runner.service; fi
fi
log 'done'
