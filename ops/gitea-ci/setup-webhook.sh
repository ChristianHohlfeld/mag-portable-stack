#!/usr/bin/env bash
# Uses ONLY the explicitly scoped credential for the one requested repository.
set +x
set -euo pipefail
[[ $EUID == 0 ]] || exit 1
tmp=$(mktemp -d /run/mag-ci-webhook.XXXXXXXX);trap 'rm -rf "$tmp"' EXIT
printf 'Authorization: Bearer %s\n' "$(tr -d '\r\n' </etc/credstore/mag-ci-github.token)" > "$tmp/header"
api=https://api.github.com/repos/ChristianHohlfeld/mag-portable-stack
call() {
 local m=$1 p=$2
 local args=(-sS --max-time 30 -o "$tmp/reply" -w '%{http_code}' -X "$m" -H @"$tmp/header" -H 'Accept: application/vnd.github+json')
 [[ $m == GET ]] || args+=(-H 'Content-Type: application/json' --data-binary @"$tmp/body")
 local code;code=$(curl "${args[@]}" "$api$p")
 [[ $code =~ ^2[0-9][0-9]$ ]] || { echo "GitHub webhook setup failed: HTTP $code" >&2;exit 1; }
}
call GET /hooks?per_page=100
id=$(jq -r '.[] | select(.config.url=="https://git.mag.stardenburg.de/mag-ci/github") | .id' "$tmp/reply")
jq -n --rawfile secret /etc/credstore/mag-ci-webhook.secret \
 '{name:"web",active:true,events:["push","pull_request"],config:{url:"https://git.mag.stardenburg.de/mag-ci/github",content_type:"json",insecure_ssl:"0",secret:($secret|gsub("[\r\n]";""))}}' > "$tmp/body"
if [[ -n $id ]]; then [[ $id =~ ^[0-9]+$ ]] || exit 1;call PATCH "/hooks/$id";else call POST /hooks;fi
