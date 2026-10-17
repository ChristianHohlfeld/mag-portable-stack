#!/usr/bin/env bash
# Narrow policy mutation helper, invoked only with literal argv after Gitea
# proves repo ownership to the bot-VM identity. No arbitrary root shell.
set -euo pipefail
[[ $EUID == 0 && $# -eq 2 ]] || exit 1
repo="$1"; mode="$2"
[[ "$repo" =~ ^magbot-[a-z][a-z0-9-]{0,31}/[a-zA-Z0-9][a-zA-Z0-9_.-]{0,79}$ &&
   "$repo" != *".."* ]] || exit 2
[[ "$mode" == public || "$mode" == tailnet ]] || exit 2
file=/etc/mag-pages/visibility.json
config=/etc/mag-pages/pages.env
[[ -f "$file" && ! -L "$file" && -r "$config" && ! -L "$config" ]] || exit 1
. "$config"
if [[ "$mode" == public && -z "$PAGES_PUBLIC_BIND" ]]; then
 echo 'Public Pages listener not configured; refusing exposure' >&2
 exit 3
fi
exec 9>/etc/mag-pages/.policy.lock
flock -x 9
jq -e 'type=="object"' "$file" >/dev/null
next="$(mktemp /etc/mag-pages/.visibility.XXXXXXXX)"
trap 'rm -f "$next"' EXIT
jq --arg repo "$repo" --arg mode "$mode" '.[$repo]=$mode' "$file" >"$next"
chmod 0640 "$next"
chown root:magbot "$next"
mv -f "$next" "$file"
echo 'Pages policy set (next C++ synchronization)'
