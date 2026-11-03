#!/usr/bin/env bash
# Persist the live KV + context checkpoints of the coding servers before a
# model restart. A turn cut by a restart is otherwise lost (live 17:19: 5.5k
# reasoning tokens never saved, next turn re-prefilled 5 653 tokens).
# Waits up to MAG_SLOT_SAVE_WAIT_S (600) for the slot to be idle, then
# POST /slots/0?action=save with the room's own prefix file name
# (mag-prefix-<alias>-slot<N>.bin, N = port - 8190). Never fails the caller.
set -uo pipefail
wait_s="${MAG_SLOT_SAVE_WAIT_S:-600}"
ports=()
while (( $# )); do
  case $1 in
    --node) ports+=("$((8191 + $2))"); shift 2 ;;
    *) ports+=("$1"); shift ;;
  esac
done
(( ${#ports[@]} )) || ports=(8191 8192)
for port in "${ports[@]}"; do
  url="http://127.0.0.1:$port"
  curl --noproxy '*' -fsS --max-time 3 -o /dev/null "$url/health" 2>/dev/null || { echo "save-slots :$port down, skipped"; continue; }
  for (( t=0; t<wait_s; t+=2 )); do
    busy=$(curl --noproxy '*' -fsS --max-time 3 "$url/slots" 2>/dev/null |
           python3 -c 'import json,sys; print(int(any(s.get("is_processing") for s in json.load(sys.stdin))))' 2>/dev/null || echo 1)
    [[ $busy == 0 ]] && break
    sleep 2
  done
  alias=$(curl --noproxy '*' -fsS --max-time 3 "$url/props" 2>/dev/null |
          python3 -c 'import json,sys; print(json.load(sys.stdin).get("model_alias",""))' 2>/dev/null)
  [[ $alias =~ ^[A-Za-z0-9._-]+$ ]] || { echo "save-slots :$port no alias, skipped"; continue; }
  file="mag-prefix-$alias-slot$((port - 8190)).bin"
  if curl --noproxy '*' -fsS --max-time 120 -H 'Content-Type: application/json' \
       -d "{\"filename\":\"$file\"}" "$url/slots/0?action=save" -o /dev/null; then
    echo "save-slots :$port saved $file"
  else
    echo "save-slots :$port save failed ($file)" >&2
  fi
done
exit 0
