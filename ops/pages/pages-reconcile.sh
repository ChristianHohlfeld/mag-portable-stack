#!/usr/bin/env bash
# Root-owned, fixed-argument systemd oneshot (not a long-running service).
set -euo pipefail
[[ $EUID == 0 ]] || exit 1
root="${MAG_PAGES_ROOT:-/var/lib/mag-pages}"
req="$root/request.json"
policy="${MAG_PAGES_POLICY_SCRIPT:-/usr/local/lib/mag-pages/pages-policy.sh}"
gc="${MAG_PAGES_GC_SCRIPT:-/usr/local/lib/mag-pages/pages-gc.sh}"
[[ -d "$root" && ! -L "$root" ]] || { echo 'Unsafe Pages root' >&2; exit 1; }
if [[ -e "$req" ]]; then
  [[ -f "$req" && ! -L "$req" ]] || exit 1
  [[ "$(stat -c %s "$req")" -le 2048 ]] || exit 1
  jq -e 'type=="object" and (keys|sort)==["action","repo"] and
         (.action=="public" or .action=="tailnet" or .action=="delete") and
         (.repo|test("^magbot-[a-z][a-z0-9-]{0,31}/[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$"))' "$req" >/dev/null
  "$policy" "$req"
fi
# Custom cert issuance and nginx reload happen NOW, not at timer-only cadence.
"$gc"
if [[ -e "$req" ]]; then rm -f -- "$req"; fi
rm -f "$root/gc.pending"
