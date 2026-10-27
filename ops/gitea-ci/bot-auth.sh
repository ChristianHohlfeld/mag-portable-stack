#!/usr/bin/env bash
# issue is safe before conversion: write the new token into the VM first;
# commit then converts the same account, retaining repos/organizations.
set +x
set -euo pipefail
mode=${1:-};user=${2:-}
[[ $EUID == 0 && $user =~ ^magbot-agent[0-9A-Za-z_-]+$ && $mode =~ ^(issue|commit|lock)$ ]] || exit 64
cli() { runuser -u git -- /usr/local/bin/gitea --config /etc/gitea/app.ini admin user "$@"; }
if [[ $mode == issue ]]; then
 if ! cli create --username "$user" --email "$user@mag.local" --user-type Bot --restricted >/dev/null 2>&1; then
  cli list | awk -v u="$user" 'NR>1&&$2==u {found=1} END {exit !found}' || exit 1
 fi
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
api PATCH "/admin/users/$user" "$session/body" >/dev/null
