#!/usr/bin/env bash
# Resume the download of ONE file of one bot:
#   fetch.sh <bot>-<model|draft>   (mag-model-fetch@<bot>-<kind>.service, user chris)
# Started by mag-modeld only when no process has the file open. wget -c
# appends to what is there, so exactly one writer per file is essential: this
# script refuses to start when anything else holds the file.
set -uo pipefail
arg=${1:?usage: fetch.sh <bot>-<model|draft>}
bot=${arg%-*}; kind=${arg##*-}
[[ $bot =~ ^[a-z][a-z0-9]{1,15}$ && ($kind == model || $kind == draft) ]] || { echo "fetch: bad argument $arg" >&2; exit 64; }
CONF=${MAG_MODELS_CONF:-/usr/local/lib/mag-models/models.json}
path=$(jq -r --arg b "$bot" --arg k "$kind" '.bots[$b][$k].path // empty' "$CONF")
url=$(jq -r --arg b "$bot" --arg k "$kind" '.bots[$b][$k].url // empty' "$CONF")
[[ -n $path && $url == https://* ]] || { echo "fetch: $arg has no download URL" >&2; exit 64; }
mkdir -p "$(dirname "$path")"
# One writer per file, also against a second copy of this script.
exec 9>"$path.fetch.lock"
flock -n 9 || { echo "fetch: $path is already being downloaded" >&2; exit 75; }
if [[ -e $path ]]; then
  real=$(readlink -f "$path")
  for fd in /proc/[0-9]*/fd/*; do
    [[ $(readlink "$fd" 2>/dev/null) == "$real" ]] || continue
    echo "fetch: another process has $path open; not touching it" >&2
    exit 75
  done
fi
exec wget -q -c --tries=0 --retry-connrefused --waitretry=15 --read-timeout=120 --timeout=60 -O "$path" "$url"
