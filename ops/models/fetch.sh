#!/usr/bin/env bash
# Resume the download of ONE file of one bot:
#   fetch.sh <bot>-<model|draft|mmproj>   (mag-model-fetch@<bot>-<kind>.service, user chris)
# Started by mag-modeld only when no process has the file open. wget -c
# appends to what is there, so exactly one writer per file is essential: this
# script refuses to start when anything else holds the file.
set -uo pipefail
arg=${1:?usage: fetch.sh <bot>-<model|draft|mmproj>}
bot=${arg%-*}; kind=${arg##*-}
[[ $bot =~ ^[a-z][a-z0-9]{1,15}$ && ($kind == model || $kind == draft || $kind == mmproj) ]] || { echo "fetch: bad argument $arg" >&2; exit 64; }
CONF=${MAG_MODELS_CONF:-/usr/local/lib/mag-models/models.json}
# Resolve the same immutable catalog profile as run.sh.
STATE=$(jq -r '.state_dir' "$CONF")
profile=default
if [[ -f "$STATE/selection.json" ]]; then
  profile=$(jq -er --arg b "$bot" '.[$b] // "default"' "$STATE/selection.json") || exit 78
fi
spec=$(jq -ce --arg b "$bot" --arg p "$profile" '
  .bots[$b] as $base |
  if $base == null then empty
  elif $p == "default" then $base
  elif .profiles[$p] == null then empty
  else ($base + {model: null, draft: null, mmproj: null, args: []}) + .profiles[$p]
  end' "$CONF") || exit 78
q() { jq -r "$1" <<<"$spec"; }
path=$(q ".$kind.path // empty")
url=$(q ".$kind.url // empty")
build=$(q ".$kind.build.type // empty")
repo=$(q ".$kind.repo // empty")
include=$(q ".$kind.include // empty")
hf_file=$(q ".$kind.file // empty")
local_dir=$(q ".$kind.local_dir // empty")
[[ -n $path && ($url == https://* || -n $build || -n $repo) ]] || { echo "fetch: $arg has no source" >&2; exit 64; }
# A staged multi-shard download must not create the final model directory:
# mag-modeld sees the first shard only after ALL shards were downloaded.
lock="$path.fetch.lock"
if [[ $kind == model && -n $repo && -n $include && -n $local_dir ]]; then lock="$local_dir.fetch.lock"; fi
mkdir -p "$(dirname "$lock")"
exec 9>"$lock"
flock -n 9 || { echo "fetch: $arg is already being downloaded" >&2; exit 75; }
if [[ -e $path ]]; then
  real=$(readlink -f "$path")
  for fd in /proc/[0-9]*/fd/*; do
    [[ $(readlink "$fd" 2>/dev/null) == "$real" ]] || continue
    echo "fetch: another process has $path open; not touching it" >&2
    exit 75
  done
fi
# Hugging Face's supported CLI uses the login belonging to the model owner.
# Never pass a token through MAG, a URL or a command argument.
if [[ -n $repo && -n $local_dir ]]; then
  command -v hf >/dev/null || { echo 'fetch: Hugging Face CLI (hf) is missing for the model owner' >&2; exit 78; }
  if [[ $kind == model && -n $include ]]; then
    staging="$local_dir.incomplete"
    if [[ -e $local_dir ]]; then
      echo "fetch: refusing to overwrite an existing model directory: $local_dir" >&2
      exit 78
    fi
    mkdir -p "$staging"
    hf download "$repo" --include "$include" --local-dir "$staging" || exit $?
    relative="${path#"$local_dir"/}"
    [[ $relative != "$path" && -s "$staging/$relative" ]] || { echo 'fetch: first GGUF shard missing' >&2; exit 78; }
    # Cross-device moves are forbidden: both directories share one parent.
    mv -T -- "$staging" "$local_dir"
    exit 0
  fi
  if [[ -n $hf_file ]]; then
    mkdir -p "$local_dir"
    exec hf download "$repo" "$hf_file" --local-dir "$local_dir"
  fi
fi
mkdir -p "$(dirname "$path")"
# A drafter published only as weights is converted here, under the same lock.
[[ -n $build ]] && exec "$(dirname "$(readlink -f "$0")")/build-draft.sh" "$bot" "$kind"
exec wget -q -c --tries=0 --retry-connrefused --waitretry=15 --read-timeout=120 --timeout=60 -O "$path" "$url"
