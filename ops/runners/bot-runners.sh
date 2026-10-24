#!/usr/bin/env bash
# Gitea Actions runners for the bot orgs only (idempotent, root). Installed as
# /usr/local/sbin/mag-gitea-bot-runners; run by ops/runners/install.sh after
# each deploy and by magbot-native after a bot account was created.
# For every bot org bots-<agent> (from the magbot-agent* users, or the
# magbot-agent*/bots-* names given as arguments):
#  - unprivileged system user magrun-bots-<agent> (group mag-botrunner, never
#    mag-deploy) with its own 0700 state dir /var/lib/mag-botrunner/<org>
#  - registration at ORG scope (`gitea actions generate-runner-token --scope
#    <org>`): the runner only gets jobs of repos owned by that org. The token
#    goes through a root-only temp dir to the runner's stdin and is deleted.
#  - an existing .runner is kept while Gitea still accepts its credentials
#    (Declare RPC); a rejected one is re-registered.
#  - mag-gitea-botrunner@<org>.service enabled and started.
# Output: one OK line per org, a WARN line per failure (exit 1). Never prints
# a token. Without arguments, instances of orgs whose bot is gone are stopped.
# MAG_BOTRUNNER_RESTART=1 restarts running instances (new binary/config/unit).
set -euo pipefail
[[ $EUID == 0 ]] || { echo 'root required' >&2; exit 1; }
read -r -a GITEA <<< "${MAG_GITEA_CLI:-/usr/sbin/runuser -u git -- /usr/local/bin/gitea --config /etc/gitea/app.ini}"
INI=${MAG_GITEA_INI:-/etc/gitea/app.ini}
RUNNER=${MAG_RUNNER_BIN:-/usr/local/bin/gitea-runner}
STATE=/var/lib/mag-botrunner
CONF=/etc/mag-botrunner/config.yaml
LABELS=mag-bot:host
warn() { echo "WARN: Gitea bot runner: $*" >&2; failed=1; }
failed=0

addr=$(sed -n 's/^HTTP_ADDR *= *//p' "$INI" | head -1); port=$(sed -n 's/^HTTP_PORT *= *//p' "$INI" | head -1)
[[ -z $addr || $addr == 0.0.0.0 ]] && addr=127.0.0.1
instance="http://$addr:${port:-3000}"
ver=$("$RUNNER" --version 2>/dev/null | awk '{print $NF}') || ver=

orgs=()
if (($#)); then
  for a in "$@"; do orgs+=("bots-${a#magbot-}"); done
  orgs=("${orgs[@]/#bots-bots-/bots-}")
else
  users=$("${GITEA[@]}" admin user list) || { warn 'cannot list Gitea users'; exit 1; }
  mapfile -t orgs < <(awk 'NR>1 && $2 ~ /^magbot-agent[0-9A-Za-z_-]*$/ {sub(/^magbot-/, "bots-", $2); print $2}' <<<"$users")
  # A removed bot leaves no runner behind (state stays for the record).
  while read -r unit _; do
    org=${unit#mag-gitea-botrunner@}; org=${org%.service}
    [[ " ${orgs[*]} " == *" $org "* ]] && continue
    systemctl disable --now "$unit" >/dev/null 2>&1 && echo "runner for $org stopped (bot gone)"
  done < <(systemctl list-units --all --plain --no-legend 'mag-gitea-botrunner@*.service' 2>/dev/null)
fi
((${#orgs[@]})) || exit 0

tmp=$(mktemp -d /run/mag-botrunner.XXXXXXXX)
trap 'rm -rf -- "$tmp"' EXIT

# valid DIR -> 0 accepted, 1 rejected by Gitea, 2 unknown (Gitea unreachable).
valid() {
  jq -er --arg i "$instance" 'select(.address == $i) | "x-runner-uuid: \(.uuid)\nx-runner-token: \(.token)"' \
    "$1/.runner" > "$tmp/hdr" 2>/dev/null || return 1
  local code
  code=$(curl -sS -o "$tmp/out" -w '%{http_code}' --max-time 10 -H @"$tmp/hdr" -H 'Content-Type: application/json' \
    --data '{"version":"'"$ver"'","labels":["'"$LABELS"'"]}' "$instance/api/actions/runner.v1.RunnerService/Declare" 2>/dev/null) || code=000
  rm -f "$tmp/hdr"
  [[ $code == 200 ]] && return 0
  grep -Eq 'Unauthenticated|unregistered runner' "$tmp/out" 2>/dev/null && return 1
  return 2
}

setup() { # org
  local org=$1 user="magrun-$1" dir="$STATE/$1" v=0
  [[ $org =~ ^bots-agent[0-9A-Za-z_-]*$ ]] || { warn "skip invalid org name $org"; return 0; }
  getent group mag-botrunner >/dev/null || groupadd --system mag-botrunner
  id "$user" >/dev/null 2>&1 ||
    useradd --system --gid mag-botrunner --no-create-home --home-dir "$dir" --shell /usr/sbin/nologin "$user"
  # Deploy rights never: drop a stale membership in mag-deploy.
  if [[ " $(id -nG "$user") " == *" mag-deploy "* ]]; then gpasswd -d "$user" mag-deploy >/dev/null; fi
  install -d -m 0755 -o root -g root "$STATE"
  install -d -m 0700 -o "$user" -g mag-botrunner "$dir"
  if [[ -s $dir/.runner ]]; then valid "$dir" || v=$?; else v=1; fi
  if ((v == 2)); then warn "$org: Gitea did not answer the runner check ($instance); kept"; fi
  if ((v == 1)); then
    # The daemon locks .runner; a rejected one is useless anyway.
    systemctl stop "mag-gitea-botrunner@$org.service" 2>/dev/null || true
    rm -f "$dir/.runner"
    if ! "${GITEA[@]}" actions generate-runner-token --scope "$org" > "$tmp/token" 2>"$tmp/err" || [[ ! -s $tmp/token ]]; then
      warn "$org: no org runner token ($(tail -1 "$tmp/err" | cut -c1-160))"; rm -f "$tmp/token"; return 0
    fi
    if ! (cd "$dir" && runuser -u "$user" -- "$RUNNER" --config "$CONF" register --no-interactive \
          --instance "$instance" --token-file /dev/stdin --name "mag-$org" --labels "$LABELS" \
          < "$tmp/token" > "$tmp/reg" 2>&1) || [[ ! -s $dir/.runner ]]; then
      rm -f "$tmp/token"
      warn "$org: registration failed ($(grep -v -i token "$tmp/reg" | tail -1 | cut -c1-160))"; return 0
    fi
    rm -f "$tmp/token"
  fi
  if ! systemctl enable --now "mag-gitea-botrunner@$org.service" >/dev/null 2>&1; then
    warn "$org: mag-gitea-botrunner@$org.service did not start"; return 0
  fi
  if [[ ${MAG_BOTRUNNER_RESTART:-0} == 1 ]]; then systemctl restart "mag-gitea-botrunner@$org.service"; fi
  ((v == 2)) || echo "OK: Gitea runner $org (org scope, label $LABELS)"
}

for org in "${orgs[@]}"; do setup "$org"; done
exit "$failed"
