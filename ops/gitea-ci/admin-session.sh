# Sourced only by root-owned provisioning helpers. No token/password in argv/logs.
set +x
set -euo pipefail
[[ $EUID == 0 ]] || exit 1
session=$(mktemp -d /run/mag-gitea-admin.XXXXXXXX);chmod 700 "$session"
admin="mag-ci-setup-$(od -An -N6 -tx1 /dev/urandom | tr -d ' \n')"
cli() { runuser -u git -- /usr/local/bin/gitea --config /etc/gitea/app.ini admin user "$@"; }
cleanup_admin() { cli delete --username "$admin" --purge >/dev/null 2>&1 || true;rm -rf "$session"; }
trap cleanup_admin EXIT
# Gitea forbids administrator Bot accounts. Reuse the existing root-only
# throw-away regular-admin pattern; no password is disclosed or web session
# created, global human 2FA is never disabled, and all credentials are purged.
cli create --username "$admin" --email "$admin@mag.local" --random-password --random-password-length 32 --admin --must-change-password=false >/dev/null
cli generate-access-token --username "$admin" --token-name setup --scopes write:admin,write:repository,write:organization,read:user --raw > "$session/token"
printf 'Authorization: token %s\n' "$(tr -d '\r\n' < "$session/token")" > "$session/header"
api() {
 local method=$1 path=$2 body=${3:-} code
 local args=(-sS --max-time 30 -o "$session/reply" -w '%{http_code}' -X "$method" -H @"$session/header" -H 'Content-Type: application/json')
 [[ -z $body ]] || args+=(--data-binary @"$body")
 code=$(curl "${args[@]}" "http://127.0.0.1:3000/api/v1$path")
 [[ $code =~ ^2[0-9][0-9]$ ]] || { echo "Gitea setup API failed: HTTP $code" >&2;return 1; }
}
