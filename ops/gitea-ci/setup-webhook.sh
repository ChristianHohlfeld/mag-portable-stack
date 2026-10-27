#!/usr/bin/env bash
# Uses ONLY the explicitly scoped credential for the one requested repository.
set +x
set -euo pipefail
[[ $EUID == 0 ]] || exit 1
hook_url=https://mag.tail3e02ae.ts.net/mag-ci/github
old_url=https://git.mag.stardenburg.de/mag-ci/github
tmp=$(mktemp -d /run/mag-ci-webhook.XXXXXXXX);trap 'rm -rf "$tmp"' EXIT
printf 'Authorization: Bearer %s\n' "$(tr -d '\r\n' </etc/credstore/mag-ci-github.token)" > "$tmp/header"
api=https://api.github.com/repos/ChristianHohlfeld/mag-portable-stack
call() {
 local m=$1 p=$2
 local args=(-sS --max-time 30 -o "$tmp/reply" -w '%{http_code}' -X "$m" -H @"$tmp/header" -H 'Accept: application/vnd.github+json')
 [[ $m == GET || $m == DELETE ]] || args+=(-H 'Content-Type: application/json' --data-binary @"$tmp/body")
 local code;code=$(curl "${args[@]}" "$api$p")
 [[ $code =~ ^2[0-9][0-9]$ ]] || { echo "GitHub webhook setup failed: HTTP $code" >&2;exit 1; }
}
call GET /hooks?per_page=100
mapfile -t ids < <(jq -r --arg new "$hook_url" --arg old "$old_url" '.[] | select(.config.url==$new or .config.url==$old) | .id' "$tmp/reply")
jq -n --rawfile secret /etc/credstore/mag-ci-webhook.secret --arg url "$hook_url" \
 '{name:"web",active:true,events:["push","pull_request"],config:{url:$url,content_type:"json",insecure_ssl:"0",secret:($secret|gsub("[\r\n]";""))}}' > "$tmp/body"
if (( ${#ids[@]} == 0 )); then
 call POST /hooks
else
 [[ ${ids[0]} =~ ^[0-9]+$ ]] || exit 1
 call PATCH "/hooks/${ids[0]}"
 for id in "${ids[@]:1}"; do
  [[ $id =~ ^[0-9]+$ ]] || exit 1
  call DELETE "/hooks/$id"
 done
fi
