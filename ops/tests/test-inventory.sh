#!/usr/bin/env bash
# mag-test-timeout: 60
if [ -z "${MAG_TEST_DEADLINE_ARMED:-}" ]; then
  export MAG_TEST_DEADLINE_ARMED=1
  exec timeout --kill-after=5s 60s bash "$0" "$@"
fi
# ops/inventory: every mapped repo source exists, and drift-check.sh reports
# OK for a faithful install, DRIFT/MISSING/WORLD otherwise (read-only).
set -euo pipefail
root=$(cd -- "$(dirname "$0")/../.." && pwd)
inv="$root/ops/inventory/host-files.tsv"
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
while IFS=$'\t' read -r kind path src; do
  [[ -z $kind || $kind == \#* ]] && continue
  [[ $kind =~ ^(copy|render|generated|secret)$ ]] || { echo "bad kind $kind"; exit 1; }
  [[ $path == /* ]] || { echo "relative path $path"; exit 1; }
  file=${src%% *}
  [[ -e $root/$file ]] || { echo "missing repo source $file for $path"; exit 1; }
  mkdir -p "$tmp/host$(dirname "$path")"
  case $kind in
    copy) cp "$root/$src" "$tmp/host$path" ;;
    render) sed "s|@ROOT@|/srv/mag|g" "$root/$src" > "$tmp/host$path" ;;
    *) echo x > "$tmp/host$path"; chmod 0640 "$tmp/host$path" ;;
  esac
done < "$inv"
check() { MAG_INVENTORY_PREFIX="$tmp/host" MAG_INVENTORY_RENDER_ROOT=/srv/mag MAG_INVENTORY_NO_SYSTEMD=1 bash "$root/ops/inventory/drift-check.sh"; }
out=$(check) || { echo "$out"; exit 1; }
! grep -Eq '^(DRIFT|MISSING|WORLD)' <<<"$out"
echo '# drift' >> "$tmp/host/usr/local/lib/mag-coding/start-live.sh"
rm "$tmp/host/etc/mag-stack/coding-engine"
chmod 0644 "$tmp/host/etc/default/mag-room"
if out=$(check); then echo 'drift not detected'; exit 1; fi
grep -Fq 'DRIFT     /usr/local/lib/mag-coding/start-live.sh' <<<"$out"
grep -Fq 'MISSING   /etc/mag-stack/coding-engine' <<<"$out"
grep -Fq 'WORLD     /etc/default/mag-room' <<<"$out"
# Never prints installed file contents.
! grep -Fq '# drift' <<<"$out"
grep -Fq 'ops/inventory/drift-check.sh' "$root/ops/inventory/README.md"
echo 'Inventory PASS'
