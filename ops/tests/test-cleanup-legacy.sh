#!/usr/bin/env bash
# Sandbox only: the one-shot cleanup removes leftovers and keeps everything
# that is referenced, in use, a model, or the stack checkout.
set -euo pipefail
root=$(cd -- "$(dirname "$0")/../.." && pwd)
t=$(mktemp -d); trap 'rm -rf "$t"' EXIT
h=$t/home; mkdir -p "$h" "$t/etc" "$t/units" "$t/tmp" "$t/full/a1" "$t/room" "$t/bin"
mkrepo() { git init -q "$1" && git -C "$1" -c user.email=t@t -c user.name=t commit -q --allow-empty -m init; }
mkrepo "$h/mag-stack"; mkrepo "$h/old-checkout"; mkrepo "$h/bneck/ik_llama.cpp"; mkrepo "$h/with-model"; mkrepo "$h/s23-native"
echo dirty > "$h/old-checkout/notes.txt"
touch "$h/with-model/x.gguf"
echo "IK_BIN=$h/bneck/ik_llama.cpp/build/bin/llama-server" > "$t/etc/llm.env"
echo "ExecStart=$h/s23-native/udp" > "$t/etc/s23-native-udp.service"
printf '#!/bin/sh\n. %s\n' "$t/lib-common.sh" > "$t/apply-iac"; echo ': common' > "$t/lib-common.sh"
echo 'old' > "$t/collect"; echo "uses $t/collect" > "$t/etc/still-used"
mkdir -p "$t/restic/data" "$t/browser-pt-1"; touch "$t/full/a1/VERIFIED"
mkdir "$t/tmp/mag-leftover" "$t/tmp/other"; touch -d '3 days ago' "$t/tmp/mag-leftover" "$t/tmp/other"
mkdir "$t/tmp/mag-fresh"
printf '[Service]\nExecStart=-%s/gone --flag\n' "$t" > "$t/units/mag-dead.service"
printf '[Service]\nExecStart=%s/apply-iac\n' "$t" > "$t/units/mag-alive.service"
printf '[Service]\nExecStart=%s/nope\n' "$t" > "$t/units/mag-llm-iq3.service"
echo '[{"id":"a","vm":"agent1"}]' > "$t/room/agents.json"
echo loose > "$h/stage-a9.sh"
echo loose > "$h/numa-bench.log"
echo loose > "$h/crontab.bak-old"
mkdir -p "$h/server-audit-20261014" "$h/s23pub" "$h/models" "$h/src/ik_llama.cpp" "$h/rtl8812au" "$h/llama-templates"
echo 'historical credential fixture' > "$h/server-audit-20261014/notes.txt"
echo keep > "$h/s23pub/s23pub.py"
echo model > "$h/models/a.gguf"
echo keep > "$h/fix-gitea.sh"
echo "uses $h/fix-gitea.sh" > "$t/etc/uses-fix"
env_common=(MAG_CLEANUP_STAMP="$t/stamp" MAG_CLEANUP_REPORT="$t/report" MAG_CLEANUP_HOME="$h"
  MAG_CLEANUP_BUNDLES="$t/bundles" MAG_ROOM_STATE="$t/room" MAG_BACKUP_ROOT="$t/full"
  MAG_CLEANUP_UNIT_DIR="$t/units" MAG_CLEANUP_TMP="$t/tmp" MAG_CLEANUP_REF_DIRS="$t/etc $t/units"
  MAG_CLEANUP_TOOLS="$t/apply-iac $t/lib-common.sh $t/collect"
  MAG_CLEANUP_BACKUPS="$t/restic $t/browser-pt-*" MAG_CLEANUP_ORPHAN_VMS=agent1 MAG_CLEANUP_MAGBOT=/bin/false
  PATH="$t/bin:$PATH")
printf '#!/bin/sh\nexit 0\n' > "$t/bin/systemctl"; chmod +x "$t/bin/systemctl"

env "${env_common[@]}" MAG_CLEANUP_DRY_RUN=1 bash "$root/ops/cleanup-legacy.sh"
test -d "$h/old-checkout" && test -d "$t/restic" && test ! -e "$t/stamp"
grep -Fq "would remove $h/old-checkout" "$t/report"

env "${env_common[@]}" bash "$root/ops/cleanup-legacy.sh"
cat "$t/report"
# removed
test ! -e "$h/old-checkout" && test -s "$t/bundles/old-checkout.bundle"
grep -Fq notes.txt "$t/bundles/old-checkout.status"
git bundle verify "$t/bundles/old-checkout.bundle" >/dev/null 2>&1
test ! -e "$t/restic" && test ! -e "$t/browser-pt-1"
test ! -e "$t/tmp/mag-leftover" && test -d "$t/tmp/mag-fresh" && test -d "$t/tmp/other"
test ! -e "$t/units/mag-dead.service"
# kept
test -d "$h/mag-stack/.git" && test -d "$h/bneck/ik_llama.cpp" && test -d "$h/with-model" && test -d "$h/s23-native"
test ! -e "$h/stage-a9.sh" && test ! -e "$h/numa-bench.log" && test ! -e "$h/crontab.bak-old"
test ! -e "$h/server-audit-20261014"
archive=$(echo "$t/bundles"/loose-home-*.tar.gz)
tar -tzf "$archive" | grep -qx 'stage-a9.sh'
tar -tzf "$archive" | grep -qx 'server-audit-20261014/notes.txt'
test -d "$h/s23pub" && test -d "$h/models" && test -d "$h/src/ik_llama.cpp" && test -d "$h/rtl8812au" && test -d "$h/llama-templates"
test -e "$h/fix-gitea.sh"
grep -Fq "kept $h/fix-gitea.sh" "$t/report"
test -e "$t/apply-iac" && test -e "$t/lib-common.sh"   # a live unit runs apply-iac
test -e "$t/units/mag-alive.service" && test -e "$t/units/mag-llm-iq3.service"
grep -Fq "kept VM agent1: used by a room bot" "$t/report"
grep -Fq "kept $t/collect: referenced by $t/etc/still-used" "$t/report"
test -e "$t/stamp"
# one-shot: a new checkout made later is not touched by the next deploy
mkrepo "$h/new-work"
echo later > "$h/stage-a-later.sh"
env "${env_common[@]}" bash "$root/ops/cleanup-legacy.sh"
test -d "$h/new-work"
test ! -e "$h/stage-a-later.sh"
! grep -n 'mag-llm' "$root/ops/cleanup-legacy.sh" | grep -v '^[0-9]*:#' | grep -vF 'mag-llm*|mag-120*' | grep -vF 'mag-llm-*' | grep -q .
echo 'cleanup-legacy PASS'
