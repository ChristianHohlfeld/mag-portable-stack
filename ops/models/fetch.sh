#!/usr/bin/env bash
# Resume the downloads of one bot (mag-model-fetch@<bot>.service, user chris,
# CPUs 28-55). Started by mag-modeld only when nobody else writes the file.
# wget -c appends to what is there; a complete file is left as it is.
set -uo pipefail
bot=${1:?usage: fetch.sh <bot>}
CONF=${MAG_MODELS_CONF:-/usr/local/lib/mag-models/models.json}
rc=0
for kind in model draft; do
  path=$(jq -r --arg b "$bot" --arg k "$kind" '.bots[$b][$k].path // empty' "$CONF")
  url=$(jq -r --arg b "$bot" --arg k "$kind" '.bots[$b][$k].url // empty' "$CONF")
  [[ -n $path && $url == https://* ]] || continue
  mkdir -p "$(dirname "$path")"
  wget -q -c --tries=0 --retry-connrefused --waitretry=15 --read-timeout=120 --timeout=60 \
    -O "$path" "$url" || rc=$?
done
exit "$rc"
