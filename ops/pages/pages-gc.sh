#!/usr/bin/env bash
# Root-owned Pages GC. Shared wildcard account and certs are NEVER removed.
set -euo pipefail
if [[ $EUID != 0 && "${MAG_PAGES_TESTING:-}" != 1 ]]; then echo 'Root required' >&2; exit 1; fi
root="${MAG_PAGES_GC_ROOT:-/var/lib/mag-pages}"
visibility="${MAG_PAGES_GC_VISIBILITY:-/etc/mag-pages/visibility.json}"
tls="${MAG_PAGES_GC_TLS:-/etc/mag-pages/tls}"
refresh="${MAG_PAGES_GC_REFRESH:-/usr/local/lib/mag-pages/refresh-custom.sh}"
manifest="$root/sites.json"
aliases="$root/custom-domains.tsv"
[[ -f "$manifest" && -f "$aliases" && ! -L "$manifest" && ! -L "$aliases" ]] ||
 { echo 'Missing authoritative Pages manifest' >&2; exit 1; }
exec 9>"$root/.gc.lock"
flock -x 9
jq -e 'type=="object" and all(.[]; .sha|type=="string")' "$manifest" >/dev/null
declare -A keep=()
while IFS=$'\t' read -r hostname canonical slug extra; do
 [[ -n "$hostname" ]] || continue
 [[ -z "$extra" && "$hostname" =~ ^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$ &&
    "$hostname" == *.* && "$hostname" != *".."* && "$slug" =~ ^[a-z0-9-]+$ &&
    "$canonical" == "$slug.pages.stardenburg.de" ]] ||
   { echo 'Invalid Pages domain manifest' >&2; exit 1; }
 hash="$(printf %s "$hostname" | sha256sum | cut -c1-16)"
 keep["$hash"]=1
done <"$aliases"
# Remove nginx references and reload BEFORE deleting the corresponding key.
"$refresh" custom-only
if [[ -d "$tls/custom" ]]; then
 for dir in "$tls/custom"/*; do
  [[ -e "$dir" || -L "$dir" ]] || continue
  [[ ! -L "$dir" && -d "$dir" ]] || { echo 'Unsafe custom cert entry' >&2; exit 1; }
  id="${dir##*/}"
  [[ "$id" =~ ^[a-f0-9]{16}$ ]] || { echo 'Foreign cert entry' >&2; exit 1; }
  if [[ ! -v keep["$id"] ]]; then rm -rf --one-file-system -- "$dir"; fi
 done
fi
if [[ -e "$visibility" ]]; then
 [[ -f "$visibility" && ! -L "$visibility" ]] || exit 1
 jq -e 'type=="object"' "$visibility" >/dev/null
 next="$(mktemp "$visibility.next.XXXXXXXX")"
 trap 'rm -f "$next"' EXIT
 jq --slurpfile live "$manifest" \
   'with_entries(select(.key as $name | $live[0] | has($name)))' \
   "$visibility" >"$next"
 if ! cmp -s "$next" "$visibility"; then
  chmod --reference="$visibility" "$next"
  chown --reference="$visibility" "$next"
  mv -f "$next" "$visibility"
 fi
fi
echo 'Pages GC complete'
