#!/usr/bin/env bash
# issue is safe before conversion: write the new token into the VM first;
# commit then converts the same account, retaining repos/organizations.
set +x
set -euo pipefail
mode=${1:-};user=${2:-}
[[ $EUID == 0 && $mode =~ ^(issue|commit|lock)$ ]] || exit 64
# Legacy VM IDs are valid for revocation only, never issuance or conversion.
if [[ $mode == lock ]]; then
 [[ $user =~ ^magbot-agent[0-9A-Za-z_-]+$ || $user =~ ^magbot-deepseek-numa[01]$ ]] || exit 64
else
 [[ $user =~ ^magbot-agent[0-9A-Za-z_-]+$ ]] || exit 64
fi
cli() { runuser -u git -- /usr/local/bin/gitea --config /etc/gitea/app.ini admin user "$@"; }
if [[ $mode == issue ]]; then
 # A VM name that was used before keeps its account. "admin user list" does
 # not show accounts of type Bot, so existence is not checked here: issuing
 # the token below fails when the account is missing.
 cli create --username "$user" --email "$user@mag.local" --user-type Bot --restricted >/dev/null 2>&1 || :
 name="vm-$(od -An -N8 -tx1 /dev/urandom | tr -d ' \n')"
 exec runuser -u git -- /usr/local/bin/gitea --config /etc/gitea/app.ini admin user \
  generate-access-token --username "$user" --token-name "$name" --scopes write:repository,write:organization,read:user --raw
fi
. /usr/local/lib/mag-ci/admin-session.sh
if [[ $mode == commit ]]; then
 /usr/local/sbin/mag-gitea-bot-rights "$user" >/dev/null
 cli change-type --username "$user" --user-type Bot >/dev/null
 jq -n '{prohibit_login:false,admin:false,restricted:true,allow_create_organization:false}' > "$session/body"
else
 # Blocks ALL tokens as well as the old password, including archived disks.
 jq -n '{prohibit_login:true,admin:false,restricted:true,allow_create_organization:false}' > "$session/body"
fi
# A missing account is already revoked, but a failed request is not proof
# of absence. Only accept PATCH 404 after independently authenticating the
# temporary admin and confirming absence on the user endpoint.
if api PATCH "/admin/users/$user" "$session/body" >/dev/null; then
 exit 0
fi
[[ $mode == lock && $api_status == 404 ]] || exit 1
api GET /user >/dev/null
jq -e --arg admin "$admin" '.is_admin == true and .login == $admin' "$session/reply" >/dev/null
if api GET "/users/$user" >/dev/null; then
 echo "Gitea account $user exists but could not be locked" >&2
 exit 1
fi
[[ $api_status == 404 ]] || exit 1
# The privileged listing includes Bot and individual accounts. Require an
# empty result as a further independent check; any ambiguity fails closed.
api GET "/admin/users?q=$user&limit=1" >/dev/null
jq -e 'type == "array" and length == 0' "$session/reply" >/dev/null
echo "Gitea account $user is absent; no credential to revoke" >&2
exit 0
