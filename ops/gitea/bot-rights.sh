#!/usr/bin/env bash
# Bots on the mag Gitea are never admins. For every magbot-agent* user (or the
# users given as arguments): admin=false, restricted=true, no org creation,
# and an own private org "bots-<user>" with the bot as owner. Bots read/write
# only there (restricted users see nothing else). Idempotent; run as root by
# ops/deploy.sh and by magbot-native after creating a bot account.
# Uses a throw-away admin account that is purged (with its token) on exit;
# no existing admin credential is read or printed.
# The org is created by that admin (POST /admin/users/{u}/orgs is refused for
# a user without allow_create_organization) and the bot joins its Owners team.
# A failing user is reported and the others are still processed; exit 1 then.
set -euo pipefail
GITEA=(${MAG_GITEA_CLI:-/usr/sbin/runuser -u git -- /usr/local/bin/gitea --config /etc/gitea/app.ini} admin user)
API=${MAG_GITEA_API:-http://127.0.0.1:3000/api/v1}
[[ -n ${MAG_GITEA_CLI:-} || -x /usr/local/bin/gitea ]] || { echo 'gitea not installed; skip'; exit 0; }
users=("$@")
if ((${#users[@]} == 0)); then
  mapfile -t users < <("${GITEA[@]}" list | awk 'NR>1 && $2 ~ /^magbot-agent[0-9A-Za-z_-]*$/ {print $2}')
fi
((${#users[@]})) || { echo 'no bot users'; exit 0; }
tmp="mag-rights-$(od -An -N4 -tx4 /dev/urandom | tr -d ' ')"
cleanup() { "${GITEA[@]}" delete --username "$tmp" --purge >/dev/null 2>&1 || true; }
trap cleanup EXIT
"${GITEA[@]}" create --username "$tmp" --email "$tmp@mag.local" --random-password \
  --admin --must-change-password=false >/dev/null
token=$("${GITEA[@]}" generate-access-token --username "$tmp" --token-name rights \
  --scopes write:admin,write:organization --raw)
# call METHOD PATH [JSON] -> HTTP status on stdout, response body in $body.
body=$(mktemp); trap 'cleanup; rm -f "$body"' EXIT
call() { curl -sS -o "$body" -w '%{http_code}' -H "Authorization: token $token" \
  -H 'Content-Type: application/json' -X "$1" "$API$2" ${3:+--data "$3"}; }
expect() { local got; got=$(call "$2" "$3" "${4:-}") || got=000
  [[ " $1 " == *" $got "* ]] || { echo "$u: $2 $3 -> HTTP $got" >&2; return 1; }; }
apply() {
  expect 200 PATCH "/admin/users/$u" '{"login_name":"'"$u"'","source_id":0,"admin":false,"restricted":true,"allow_create_organization":false,"max_repo_creation":50}' || return 1
  local org="bots-${u#magbot-}" code team created=0
  code=$(call GET "/orgs/$org") || code=000
  case $code in
    200) ;;
    404) expect 201 POST /orgs '{"username":"'"$org"'","visibility":"private","repo_admin_change_team_access":false}' || return 1
         created=1 ;;
    *) echo "$u: GET /orgs/$org -> HTTP $code" >&2; return 1 ;;
  esac
  expect 200 GET "/orgs/$org/teams" || return 1
  team=$(jq -er '.[] | select(.name == "Owners") | .id' "$body") || { echo "$u: $org has no Owners team" >&2; return 1; }
  expect 204 PUT "/teams/$team/members/$u" || return 1
  # The throw-away admin leaves the new org now that the bot is an owner.
  if ((created)); then expect 204 DELETE "/orgs/$org/members/$tmp" || return 1; fi
  echo "$u: admin=false restricted=true org=$org owner"
}
failed=0
for u in "${users[@]}"; do
  [[ $u =~ ^magbot-agent[0-9A-Za-z_-]*$ ]] || { echo "skip $u"; continue; }
  apply || { echo "ERROR: Gitea rights for $u not applied" >&2; failed=1; }
done
exit "$failed"
