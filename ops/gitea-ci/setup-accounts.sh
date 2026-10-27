#!/usr/bin/env bash
set +x
set -euo pipefail
. /usr/local/lib/mag-ci/admin-session.sh
if ! cli list | awk 'NR>1&&$2=="chris" {found=1} END {exit !found}'; then
 cli create --username chris --email chris@mag.local --admin --random-password --random-password-length 32 --must-change-password=true > "$session/create"
 sed -n "s/^generated random password is '\(.*\)'$/\1/p" "$session/create" > /etc/credstore/gitea-chris.password
 chmod 600 /etc/credstore/gitea-chris.password
 [[ -s /etc/credstore/gitea-chris.password ]] || exit 1
fi
printf '%s\n' '{"admin":true}' > "$session/body";api PATCH /admin/users/chris "$session/body"
# A dedicated noninteractive OWNER of exactly one repository, not an admin or
# organization owner with access to unrelated projects. Name conflicts fail.
if ! cli list | awk 'NR>1&&$2=="mag-ci" {found=1} END {exit !found}'; then
 cli create --username mag-ci --email mag-ci@mag.local --user-type Bot --restricted >/dev/null
fi
cli change-type --username mag-ci --user-type Bot >/dev/null
printf '%s\n' '{"admin":false,"restricted":true,"allow_create_organization":false,"max_repo_creation":1}' > "$session/body"
api PATCH /admin/users/mag-ci "$session/body"
if [[ ! -s /etc/credstore/mag-ci-gitea.token ]]; then
 cli generate-access-token --username mag-ci --token-name mag-ci-controller --scopes write:repository,read:user --raw > /etc/credstore/mag-ci-gitea.token
 chmod 600 /etc/credstore/mag-ci-gitea.token
fi
printf 'Authorization: token %s\n' "$(tr -d '\r\n' </etc/credstore/mag-ci-gitea.token)" > "$session/controller-header"
# Use the Bot's own token for repository lookup: globally enforced 2FA also
# denies repository API calls from unenrolled regular users, including setup.
code=$(curl -sS --max-time 15 -o "$session/repo" -w '%{http_code}' -H @"$session/controller-header" http://127.0.0.1:3000/api/v1/repos/mag-ci/mag-portable-stack)
if [[ $code == 404 ]]; then
 printf '%s\n' '{"name":"mag-portable-stack","description":"MAG CI mirror of ChristianHohlfeld/mag-portable-stack","private":true,"has_actions":true,"has_issues":false,"has_wiki":false,"default_branch":"main","auto_init":false}' > "$session/body"
 api POST /admin/users/mag-ci/repos "$session/body"
else
 [[ $code == 200 ]] && jq -e '.mirror==false and .description=="MAG CI mirror of ChristianHohlfeld/mag-portable-stack"' "$session/repo" >/dev/null || { echo 'Refusing to take over an unrelated/existing mirror repository' >&2;exit 1; }
fi

printf '%s\n' '{"has_actions":true}' > "$session/body"
cp "$session/header" "$session/admin-header"
cp "$session/controller-header" "$session/header"
api PATCH /repos/mag-ci/mag-portable-stack "$session/body"
cp "$session/admin-header" "$session/header"
