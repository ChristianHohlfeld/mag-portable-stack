#!/usr/bin/env bash
# Gitea Actions runner status (root; installed as
# /usr/local/sbin/mag-gitea-runner-status). Read-only for runners and repos:
# prints whether Actions are on, and per bot org the local unit, the
# registration and what Gitea reports (online/offline, labels). Any runner
# outside the bot orgs (instance-wide or another owner) is flagged.
# Gitea's view needs an admin API token: like ops/gitea/bot-rights.sh it uses a
# throw-away admin (read-only scopes) purged with its token on exit. No token
# is printed. Exit 1 when something is wrong.
set -euo pipefail
read -r -a GITEA <<< "${MAG_GITEA_CLI:-/usr/sbin/runuser -u git -- /usr/local/bin/gitea --config /etc/gitea/app.ini}"
INI=${MAG_GITEA_INI:-/etc/gitea/app.ini}
STATE=/var/lib/mag-botrunner
addr=$(sed -n 's/^HTTP_ADDR *= *//p' "$INI" | head -1); port=$(sed -n 's/^HTTP_PORT *= *//p' "$INI" | head -1)
[[ -z $addr || $addr == 0.0.0.0 ]] && addr=127.0.0.1
base="http://$addr:${port:-3000}"
bad=0
enabled=$(awk '/^[ \t]*\[/ {s = ($0 ~ /\[actions\]/)} s && toupper($0) ~ /^[ \t]*ENABLED[ \t]*=/ {sub(/.*=[ \t]*/, ""); v = $0} END {print v}' "$INI")
ping=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 5 -X POST -H 'Content-Type: application/json' \
  --data '{"data":"status"}' "$base/api/actions/ping.v1.PingService/Ping" 2>/dev/null) || ping=000
echo "actions: app.ini ENABLED=${enabled:-unset}, runner API HTTP $ping"
[[ $ping == 200 ]] || bad=1

tmp=$(mktemp -d)
adm="mag-status-$(od -An -N4 -tx4 /dev/urandom | tr -d ' ')"
cleanup() { "${GITEA[@]}" admin user delete --username "$adm" --purge >/dev/null 2>&1 || true; rm -rf -- "$tmp"; }
trap cleanup EXIT
users=$("${GITEA[@]}" admin user list) || { echo 'cannot list Gitea users'; exit 1; }
mapfile -t orgs < <(awk 'NR>1 && $2 ~ /^magbot-agent[0-9A-Za-z_-]*$/ {sub(/^magbot-/, "bots-", $2); print $2}' <<<"$users")
api=0
if "${GITEA[@]}" admin user create --username "$adm" --email "$adm@mag.local" --random-password \
     --admin --must-change-password=false >/dev/null 2>&1 &&
   "${GITEA[@]}" admin user generate-access-token --username "$adm" --token-name status \
     --scopes read:admin,read:organization --raw 2>/dev/null | sed 's/^/Authorization: token /' > "$tmp/auth" &&
   [[ -s $tmp/auth ]]; then api=1; fi
get() { curl -sS -f --max-time 10 -H @"$tmp/auth" "$base/api/v1$1" 2>/dev/null; }

: > "$tmp/known"
for org in "${orgs[@]}"; do
  unit=$(systemctl is-active "mag-gitea-botrunner@$org.service" 2>/dev/null) || true
  reg=no; [[ -s $STATE/$org/.runner ]] && reg=yes
  view='gitea=unknown'
  if ((api)) && get "/orgs/$org/actions/runners" > "$tmp/r.json"; then
    jq -r '.runners[].id' "$tmp/r.json" >> "$tmp/known"
    view=$(jq -r 'if (.runners | length) == 0 then "gitea=no-runner" else
      [.runners[] | "gitea=\(.status) name=\(.name) labels=\([.labels[].name] | join(","))"] | join("; ") end' "$tmp/r.json")
  fi
  echo "$org: unit=${unit:-inactive} registered=$reg $view"
  [[ $unit == active && $reg == yes && ( $view == *gitea=online* || $view == *gitea=idle* || $view == *gitea=active* ) ]] || bad=1
done
((${#orgs[@]})) || echo 'no bot users'
if ((api)) && get /admin/actions/runners > "$tmp/all.json"; then
  others=$(jq -r --slurpfile k <(jq -s . "$tmp/known") \
    '.runners[] | select(.id as $i | $k[0] | any(.[]; . == $i) | not) | "\(.name) (\(.status))"' "$tmp/all.json")
  if [[ -n $others ]]; then
    echo "runners outside the bot orgs (instance-wide or other owners):"; sed 's/^/  /' <<<"$others"
    [[ -s /etc/mag-stack/host-runner.owner ]] && echo "  (host runner opted in for $(cat /etc/mag-stack/host-runner.owner))" || bad=1
  else
    echo 'runners outside the bot orgs: none'
  fi
else
  echo 'gitea API view unavailable'; bad=1
fi
exit "$bad"
