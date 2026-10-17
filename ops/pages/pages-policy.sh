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
# Fail closed at policy commit time. Do NOT wait for the C++ republisher to
# remove old public links; an API/token failure must never keep public access.
if [[ "$mode" == tailnet || "$mode" == delete ]]; then
  root="${MAG_PAGES_POLICY_ROOT:-/var/lib/mag-pages}"
  manifest="$root/sites.json"
  if [[ -f "$manifest" && ! -L "$manifest" ]]; then
    slug="$(jq -r --arg repo "$repo" '.[$repo].slug // empty' "$manifest")"
    custom="$(jq -r --arg repo "$repo" '.[$repo].custom // empty' "$manifest")"
    if [[ -n "$slug" ]]; then
      [[ "$slug" =~ ^[a-z0-9][a-z0-9-]{0,62}$ ]] || exit 1
      [[ ! -L "$root/public" && ! -L "$root/tailnet" && ! -L "$root/custom" ]] || exit 1
      [[ ! -e "$root/public/$slug" || -L "$root/public/$slug" ]] || exit 1
      if [[ -L "$root/public/$slug" ]]; then rm -f -- "$root/public/$slug"; fi
      if [[ "$mode" == delete ]]; then
        if [[ -L "$root/tailnet/$slug" ]]; then rm -f -- "$root/tailnet/$slug"; fi
      fi
    fi
    if [[ -n "$custom" ]]; then
      [[ "$custom" =~ ^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$ &&
         "$custom" == *.* && "$custom" != *".."* ]] || exit 1
      if [[ -L "$root/custom/$custom" ]]; then rm -f -- "$root/custom/$custom"; fi
    fi
  fi
fi
echo 'Pages policy set; forbidden public routes revoked immediately'
