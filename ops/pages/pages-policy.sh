#!/usr/bin/env bash
# Narrow policy mutation helper, invoked only with literal argv after Gitea
# proves repo ownership to the bot-VM identity. No arbitrary root shell.
set -euo pipefail
[[ $EUID == 0 && $# -eq 1 ]] || exit 1
request="$1"
[[ "$request" == /var/lib/mag-pages/request.json || "${MAG_PAGES_TESTING:-}" == 1 ]] || exit 2
[[ -f "$request" && ! -L "$request" && "$(stat -c %s "$request")" -le 2048 ]] || exit 2
jq -e 'type=="object" and (keys|sort)==["action","repo"]' "$request" >/dev/null
repo="$(jq -r .repo "$request")"
mode="$(jq -r .action "$request")"
[[ "$repo" =~ ^magbot-[a-z][a-z0-9-]{0,31}/[a-zA-Z0-9][a-zA-Z0-9_.-]{0,79}$ &&
   "$repo" != *".."* ]] || exit 2
[[ "$mode" == public || "$mode" == tailnet || "$mode" == delete ]] || exit 2
file="${MAG_PAGES_POLICY_FILE:-/etc/mag-pages/visibility.json}"
config="${MAG_PAGES_CONFIG_FILE:-/etc/mag-pages/pages.env}"
[[ -f "$file" && ! -L "$file" && -r "$config" && ! -L "$config" ]] || exit 1
. "$config"
if [[ "$mode" == public && -z "$PAGES_PUBLIC_BIND" ]]; then
 echo 'Public Pages listener not configured; refusing exposure' >&2
 exit 3
fi
exec 9>"$(dirname "$file")/.policy.lock"
flock -x 9
jq -e 'type=="object"' "$file" >/dev/null
next="$(mktemp "$(dirname "$file")/.visibility.XXXXXXXX")"
trap 'rm -f "$next"' EXIT
if [[ "$mode" == delete ]]; then
 jq --arg repo "$repo" 'del(.[$repo])' "$file" >"$next"
else
 jq --arg repo "$repo" --arg mode "$mode" '.[$repo]=$mode' "$file" >"$next"
fi
chmod 0640 "$next"
if [[ "${MAG_PAGES_TESTING:-}" != 1 ]]; then chown root:magbot "$next"; fi
mv -f "$next" "$file"
echo 'Pages policy set (next C++ synchronization)'
