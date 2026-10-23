#!/usr/bin/env bash
# Bots on the mag Gitea are never admins. For every magbot-agent* user (or the
# users given as arguments): admin=false, restricted=true, no org creation,
# and an own private org "bots-<user>" with the bot as owner. Bots read/write
# only there (restricted users see nothing else). Idempotent; run as root by
# ops/deploy.sh and by magbot-native after creating a bot account.
# Uses a throw-away admin account that is purged (with its token) on exit;
# no existing admin credential is read or printed.
set -euo pipefail
GITEA=(/usr/sbin/runuser -u git -- /usr/local/bin/gitea --config /etc/gitea/app.ini admin user)
API=${MAG_GITEA_API:-http://127.0.0.1:3000/api/v1}
[[ -x /usr/local/bin/gitea ]] || { echo 'gitea not installed; skip'; exit 0; }
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
call() { curl -fsS -o /dev/null -w '%{http_code}' -H "Authorization: token $token" \
  -H 'Content-Type: application/json' -X "$1" "$API$2" ${3:+--data "$3"}; }
for u in "${users[@]}"; do
  [[ $u =~ ^magbot-agent[0-9A-Za-z_-]*$ ]] || { echo "skip $u"; continue; }
  call PATCH "/admin/users/$u" '{"login_name":"'"$u"'","source_id":0,"admin":false,"restricted":true,"allow_create_organization":false,"max_repo_creation":50}' >/dev/null
  org="bots-${u#magbot-}"
  if ! curl -fsS -o /dev/null -H "Authorization: token $token" "$API/orgs/$org"; then
    call POST "/admin/users/$u/orgs" '{"username":"'"$org"'","visibility":"private","repo_admin_change_team_access":false}' >/dev/null
  fi
  echo "$u: admin=false restricted=true org=$org"
done
