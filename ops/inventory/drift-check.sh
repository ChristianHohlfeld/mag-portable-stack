#!/usr/bin/env bash
# Read-only drift check: installed host files and boot state vs. this checkout.
# Never starts, stops, reloads or writes anything and never prints file
# contents (secret files are only tested for existence and mode).
#   sudo bash ops/inventory/drift-check.sh          # full report, exit 1 on drift
#   MAG_INVENTORY_PREFIX=/tmp/x  test root for installed paths (tests only)
#   MAG_INVENTORY_NO_SYSTEMD=1   skip the systemd/boot section (tests only)
set -uo pipefail
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
INV="$ROOT/ops/inventory"
PREFIX=${MAG_INVENTORY_PREFIX:-}
RENDER_ROOT=${MAG_INVENTORY_RENDER_ROOT:-$ROOT}
bad=0
say() { printf '%-9s %s\n' "$1" "$2"; }
sha() { sha256sum < "$1" | cut -c1-64; }

while IFS=$'\t' read -r kind path src; do
  [[ -z $kind || $kind == \#* ]] && continue
  host="$PREFIX$path"
  if [[ ! -e $host ]]; then
    say MISSING "$path ($src)"; bad=1; continue
  fi
  case "$kind" in
    copy)
      if [[ $(sha "$host") == $(sha "$ROOT/$src") ]]; then say OK "$path"
      else say DRIFT "$path != $src"; bad=1; fi ;;
    render)
      if [[ $(sha "$host") == $(sed "s|@ROOT@|$RENDER_ROOT|g" "$ROOT/$src" | sha256sum | cut -c1-64) ]]; then say OK "$path"
      else say DRIFT "$path != $src (@ROOT@=$RENDER_ROOT)"; bad=1; fi ;;
    generated) say PRESENT "$path (from $src)" ;;
    secret)
      mode=$(stat -c %a "$host")
      if (( 8#$mode & 8#007 )); then say WORLD "$path is world-accessible ($mode)"; bad=1
      else say SECRET "$path ($mode, restore from backup)"; fi ;;
    *) say UNKNOWN "$kind $path"; bad=1 ;;
  esac
done < "$INV/host-files.tsv"

if [[ ${MAG_INVENTORY_NO_SYSTEMD:-0} != 1 ]]; then
  while IFS=$'\t' read -r unit want; do
    [[ -z $unit || $unit == \#* ]] && continue
    got=$(systemctl is-enabled "$unit" 2>/dev/null || true)
    [[ -z $got ]] && got=absent
    if [[ $want == enabled && $got =~ ^(enabled|static|indirect|alias)$ ]] ||
       [[ $want == disabled && $got =~ ^(disabled|absent|not-found|masked)$ ]]; then
      say BOOT "$unit $got"
    else
      say BOOTERR "$unit is $got, want $want"; bad=1
    fi
  done < "$INV/boot-units.txt"
  # Ordering that must hold on a cold boot.
  for i in 0 1; do
    if systemctl show -p After --value "mag-llm-coding@$i.service" | tr ' ' '\n' | grep -Fxq mag-cpu-tune.service; then
      say ORDER "mag-cpu-tune before mag-llm-coding@$i"
    else
      say ORDERERR "mag-llm-coding@$i not ordered after mag-cpu-tune"; bad=1
    fi
  done
  for dep in mag-audit-gateway.service mag-audit-nft.service mag-audit-webproxy.service; do
    if systemctl show -p After --value mag-120.service | tr ' ' '\n' | grep -Fxq "$dep"; then
      say ORDER "$dep before mag-120"
    else
      say ORDERERR "mag-120 not ordered after $dep"; bad=1
    fi
  done
  # Pinned runtime the launcher will exec on the next start.
  rev=$(cat "$ROOT/ops/coding/runtime.sha")
  bin="/home/chris/.local/mag-coding-rt/$rev/bin/llama-server"
  if [[ -x $bin && $(cat "$(dirname "$bin")/source-commit" 2>/dev/null) == "$rev" ]]; then
    want=$(awk -v r="$rev" '$1=="llama-server" && $2==r {print $4}' "$INV/runtimes.lock")
    if [[ -n $want && $(sha "$bin") == "$want" ]]; then say OK "$bin (runtimes.lock)"
    else say DRIFT "$bin sha256 differs from runtimes.lock"; bad=1; fi
  else
    say MISSING "$bin (build: ops/coding/build-runtime.sh)"; bad=1
  fi
fi
exit "$bad"
