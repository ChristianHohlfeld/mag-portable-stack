#!/usr/bin/env bash
# ops/magbot lifecycle against a fake virsh/qemu-img/virt-install/
# cloud-localds/gitea/systemctl layer (no KVM, no root, no network).
set -euo pipefail
REPO=$(cd -- "$(dirname -- "$0")/../.." && pwd)
MAGBOT=$REPO/ops/magbot
T=$(mktemp -d)
trap 'rm -rf -- "$T"' EXIT
BIN=$T/bin; FS=$T/fake; mkdir -p "$BIN" "$FS/domains" "$FS/gitea" "$T/units"
export FAKE_STATE=$FS PATH="$BIN:$PATH"

cat >"$BIN/virsh" <<'EOF'
#!/usr/bin/env bash
[[ $1 == -c ]] && shift 2
cmd=$1; shift
d=$FAKE_STATE/domains
echo "virsh $cmd $*" >>"$FAKE_STATE/log"
case $cmd in
  dominfo) [[ -f $d/$1 ]] ;;
  domstate) [[ -f $d/$1 ]] && cat "$d/$1" ;;
  destroy) [[ -f $d/$1 ]] && echo "shut off" >"$d/$1" ;;
  undefine) [[ -f $d/$1 ]] && rm -f "$d/$1" ;;
  list) ls "$d" ;;
  schedinfo|blkdeviotune|autostart|net-start|net-update) [[ $cmd == net-* || -f $d/$1 ]] ;;
  net-info) printf 'Name: default\nActive:         yes\n' ;;
  net-dumpxml) echo "<network><name>default</name></network>" ;;
  qemu-agent-command)
    [[ -f $d/$1 ]] || exit 1
    case $2 in
      *'"guest-exec"'*) echo '{"return":{"pid":42}}' ;;
      *) echo "{\"return\":{\"exited\":true,\"exitcode\":${FAKE_GUEST_RC:-0}}}" ;;
    esac ;;
  *) echo "fake virsh: unsupported $cmd" >&2; exit 1 ;;
esac
EOF
cat >"$BIN/qemu-img" <<'EOF'
#!/usr/bin/env bash
echo "qemu-img $*" >>"$FAKE_STATE/log"
if [[ $1 == create ]]; then
  while [[ $1 != -b ]]; do shift; done
  out=$3
  [[ -e $out ]] && { echo "exists: $out" >&2; exit 1; }
  printf 'overlay backing=%s\n' "$2" >"$out"
elif [[ $1 == info ]]; then
  f=${!#}; [[ -f $f ]] || exit 1
  printf '{"backing-filename": "%s", "format": "qcow2"}\n' "$(sed -n 's/^overlay backing=//p' "$f")"
fi
EOF
cat >"$BIN/virt-install" <<'EOF'
#!/usr/bin/env bash
while [[ $# -gt 0 ]]; do [[ $1 == --name ]] && name=$2; shift; done
echo "virt-install $name" >>"$FAKE_STATE/log"
sleep "${FAKE_DEFINE_SLEEP:-0}"
[[ -e $FAKE_STATE/domains/$name ]] && { echo "domain exists" >&2; exit 1; }
echo running >"$FAKE_STATE/domains/$name"
EOF
cat >"$BIN/cloud-localds" <<'EOF'
#!/usr/bin/env bash
[[ $1 == --network-config ]] && shift 2
out=$1; shift
cat "$@" >"$out"
EOF
cat >"$BIN/gitea" <<'EOF'
#!/usr/bin/env bash
shift 2   # admin user
cmd=$1; shift
case $cmd in
  list) echo "ID Username Email IsActive IsAdmin 2FA"; i=1
        for u in "$FAKE_STATE"/gitea/*; do [[ -e $u ]] && echo "$((i++)) $(basename "$u") x@mag.local true false false"; done ;;
  create) while [[ $# -gt 0 ]]; do case $1 in --username) u=$2;; --password) p=$2;; esac; shift; done
          [[ -e $FAKE_STATE/gitea/$u ]] && exit 1; printf %s "$p" >"$FAKE_STATE/gitea/$u" ;;
  delete) [[ $1 == --username ]] && rm "$FAKE_STATE/gitea/$2" ;;
esac
exit 0
EOF
printf '#!/bin/sh\nexit 0\n' >"$BIN/systemctl"
printf '#!/bin/sh\nexit 0\n' >"$BIN/gitea-rights"
cat >"$BIN/systemd-run" <<'EOF'
#!/usr/bin/env bash
# Records the transient unit and runs the command with its --setenv values.
while [[ $1 == --* ]]; do
  case $1 in --unit=*) echo "unit ${1#--unit=}" >>"$FAKE_STATE/log" ;; --setenv=*=*) export "${1#--setenv=}" ;; esac
  shift
done
exec "$@"
EOF
chmod +x "$BIN"/*

export MAGBOT_ROOT=$T/root MAGBOT_LOCK_DIR=$T/locks MAGBOT_UNIT_DIR=$T/units \
  MAGBOT_ENV_FILE=/nonexistent MAGBOT_GITEA=$BIN/gitea MAGBOT_GITEA_RIGHTS=$BIN/gitea-rights \
  MAGBOT_DISK_GROUP='' MAGBOT_POLL=0 MAGBOT_BOOT_TIMEOUT=5 MAGBOT_ALLOW_NONROOT=1 \
  MAGBOT_FOREGROUND=1 MAGBOT_BUILD_GOLDEN=/bin/false
mkdir -p "$MAGBOT_ROOT/base"
GOLDEN=$MAGBOT_ROOT/base/golden.qcow2
echo golden-bytes >"$GOLDEN"; chmod 0444 "$GOLDEN"
GOLDEN_SUM=$(sha256sum "$GOLDEN")

fail() { echo "FAIL: $*" >&2; exit 1; }
leak_check() { # nothing with prefix magbot-<name> may remain
  local n=$1
  [[ ! -e $FAKE_STATE/domains/magbot-$n ]] || fail "domain magbot-$n leaked"
  [[ ! -e $MAGBOT_ROOT/magbot-$n ]] || fail "dir magbot-$n leaked"
  [[ ! -e $FAKE_STATE/gitea/magbot-$n ]] || fail "gitea user magbot-$n leaked"
  ! compgen -G "$T/units/magbot-$n*" >/dev/null || fail "unit magbot-$n leaked"
  "$MAGBOT" verify "$n" >/dev/null || fail "verify reports leftovers for $n"
}

# 1. roundtrip create -> verify -> delete, seed content, no secret in output
"$MAGBOT" create alpha >"$T/out" 2>&1 || { cat "$T/out"; fail "create alpha"; }
"$MAGBOT" verify alpha | grep -q '^OK magbot-alpha present' || fail "verify alpha"
seed=$MAGBOT_ROOT/magbot-alpha/magbot-alpha-seed.iso
grep -q "$(base64 -w0 <"$REPO/mag-room/native/bot_context.md")" "$seed" || fail "MAG_CONTEXT.md not in seed"
grep -q "path: /etc/magbot/llm.env" "$seed" || fail "LLM config not in seed"
[[ $(stat -c %a "$seed") == 640 ]] || fail "seed mode"
pass=$(cat "$FAKE_STATE/gitea/magbot-alpha")
grep -q "$(printf 'http://magbot-alpha:%s@192.168.122.1:3001\n' "$pass" | base64 -w0)" "$seed" || fail "credential not in seed"
! grep -q "$pass" "$T/out" || fail "credential printed"
[[ ! -e $MAGBOT_ROOT/magbot-alpha/user-data ]] || fail "plaintext user-data kept"
grep -q "\"state\": \"ready\"" "$MAGBOT_ROOT/magbot-alpha/metadata.json" || fail "metadata not ready"
"$MAGBOT" list | grep -q '^alpha .*ready' || fail "list"
touch "$T/units/magbot-alpha-browser.service"
FAKE_GUEST_RC=1 "$MAGBOT" verify alpha >/dev/null && fail "verify must fail when guest checks fail"
"$MAGBOT" delete alpha >/dev/null 2>&1 || fail "delete alpha"
leak_check alpha
echo "ok roundtrip"

# 2. idempotency
"$MAGBOT" create beta >/dev/null 2>&1 || fail "create beta"
"$MAGBOT" create beta >/dev/null 2>&1 || fail "double create"
[[ $(grep -c '^virt-install magbot-beta$' "$FAKE_STATE/log") == 1 ]] || fail "double create defined twice"
"$MAGBOT" delete beta >/dev/null 2>&1 || fail "delete beta"
"$MAGBOT" delete beta >/dev/null 2>&1 || fail "double delete"
leak_check beta
"$MAGBOT" delete never-existed >/dev/null 2>&1 || fail "delete of absent bot"
# half-created remains (old magbot-agent2 case) are swept by create
mkdir -p "$MAGBOT_ROOT/magbot-gamma"; echo "shut off" >"$FAKE_STATE/domains/magbot-gamma"
"$MAGBOT" verify gamma >/dev/null && fail "verify must flag half-created bot"
"$MAGBOT" create gamma >/dev/null 2>&1 || fail "create over remains"
"$MAGBOT" verify gamma >/dev/null || fail "verify gamma"
"$MAGBOT" reset gamma >/dev/null 2>&1 || fail "reset"
"$MAGBOT" verify gamma >/dev/null || fail "verify after reset"
"$MAGBOT" delete gamma >/dev/null 2>&1; leak_check gamma
echo "ok idempotency/reset"

# 3. FAIL_AT at every step rolls back completely
for step in lock golden dir overlay gitea seed define tune boot verify; do
  FAIL_AT=$step "$MAGBOT" create delta >"$T/out" 2>&1 && fail "FAIL_AT=$step succeeded"
  grep -q "injected failure after step '$step'" "$T/out" || { cat "$T/out"; fail "FAIL_AT=$step not reached"; }
  leak_check delta
done
FAKE_GUEST_RC=1 "$MAGBOT" create delta >/dev/null 2>&1 && fail "boot timeout must fail"
leak_check delta
"$MAGBOT" create 'Bad_Name' >/dev/null 2>&1 && fail "invalid name accepted"
echo "ok rollback"

# 4. parallel: 3 names succeed; same name is serialised by flock
pids=()
for n in p1 p2 p3; do FAKE_DEFINE_SLEEP=1 "$MAGBOT" create "$n" >"$T/$n.out" 2>&1 & pids+=($!); done
for p in "${pids[@]}"; do wait "$p" || fail "parallel create"; done
for n in p1 p2 p3; do "$MAGBOT" verify "$n" >/dev/null || fail "verify $n"; done
FAKE_DEFINE_SLEEP=1 "$MAGBOT" create same >"$T/s1" 2>&1 & a=$!
FAKE_DEFINE_SLEEP=1 "$MAGBOT" create same >"$T/s2" 2>&1 & b=$!
wait "$a" || fail "same-name create 1"; wait "$b" || fail "same-name create 2"
[[ $(grep -c '^virt-install magbot-same$' "$FAKE_STATE/log") == 1 ]] || fail "same-name create not serialised"
grep -q 'already exists' "$T/s1" "$T/s2" || fail "second same-name create did not see the first"
for n in p1 p2 p3 same; do "$MAGBOT" delete "$n" >/dev/null 2>&1; leak_check "$n"; done
echo "ok parallel"

# 5. job mode goes through systemd-run --unit=magbot-<name>-job
MAGBOT_FOREGROUND=0 "$MAGBOT" create jobbot >/dev/null 2>&1 || fail "job create"
grep -q '^unit magbot-jobbot-job$' "$FAKE_STATE/log" || fail "no transient unit"
MAGBOT_FOREGROUND=0 "$MAGBOT" delete jobbot >/dev/null 2>&1; leak_check jobbot
echo "ok job"

# 6. room entry point (magbot-agent-ctl) -> this CLI: spawn/reset/despawn
cat >"$BIN/magbot-native" <<'EOF'
#!/usr/bin/env bash
echo "native $*" >>"$FAKE_STATE/log"
EOF
chmod +x "$BIN/magbot-native"
room_ctl() {
  MAG_BOTS_MAGBOTCTL=$MAGBOT MAG_BOTS_MAGBOT=$BIN/magbot-native MAG_BOTS_SITE=/nonexistent \
    MAGBOT_ENV_FILE=/nonexistent "$REPO/ops/magbot-agent-ctl" "$@"
}
room_ctl spawn bot1 >/dev/null 2>&1 || fail "room spawn bot1"
"$MAGBOT" verify bot1 >/dev/null || fail "room-created bot1 does not verify"
room_ctl verify bot1 | grep -q 'MAG_VM_METADATA_OK bot1' || fail "room verify bot1"
room_ctl spawn bot1 >"$T/out" 2>&1 && fail "fresh spawn adopted an existing bot"
grep -q 'already exists' "$T/out" || fail "spawn over existing must say already exists"
# reset = delete + create: the workspace (overlay) is replaced, not reused
ov=$MAGBOT_ROOT/magbot-bot1/magbot-bot1.qcow2
echo "workspace-file-from-old-life" >>"$ov"
created=$(sed -n 's/.*"created_at": \([0-9]*\).*/\1/p' "$MAGBOT_ROOT/magbot-bot1/metadata.json")
room_ctl reset bot1 >/dev/null 2>&1 || fail "room reset bot1"
! grep -q workspace-file-from-old-life "$ov" || fail "reset kept the old workspace"
[[ $(grep -c '^virt-install magbot-bot1$' "$FAKE_STATE/log") == 2 ]] || fail "reset did not re-create"
"$MAGBOT" verify bot1 >/dev/null || fail "verify after room reset"
[[ -n $created ]] || fail "no created_at"
# failed create (any step) leaves nothing, and the error reaches the caller
FAIL_AT=boot room_ctl spawn bot2 >"$T/out" 2>&1 && fail "failed room spawn reported success"
grep -q "injected failure after step 'boot'" "$T/out" || fail "room spawn error not surfaced"
leak_check bot2
room_ctl despawn bot1 >/dev/null 2>&1 || fail "room despawn bot1"
leak_check bot1
rc=0; room_ctl verify bot1 >/dev/null 2>&1 || rc=$?; [[ $rc == 3 ]] || fail "verify of deleted bot must exit 3"
echo "ok room entry point"

# 7. one-shot migration of legacy agent1/agent3 (fake native registry)
cat >"$BIN/magbot-native" <<'EOF'
#!/usr/bin/env bash
echo "native $*" >>"$FAKE_STATE/log"
[[ $1 == despawn ]] || exit 0
[[ ${FAKE_NATIVE_FAIL:-} == "$2" ]] && { echo "fake teardown failure" >&2; exit 1; }
rm -rf "$MAGBOT_ROOT/bots/$2" "$FAKE_STATE/domains/magbot-$2" "$FAKE_STATE/gitea/magbot-$2" "$MAGBOT_UNIT_DIR/magbot-$2.service"
EOF
for id in agent1 agent3; do
  mkdir -p "$MAGBOT_ROOT/bots/$id"; echo "{\"id\":\"$id\"}" >"$MAGBOT_ROOT/bots/$id/metadata.json"
  echo running >"$FAKE_STATE/domains/magbot-$id"; echo pw >"$FAKE_STATE/gitea/magbot-$id"
  touch "$T/units/magbot-$id.service"
done
migrate() {
  MAG_MIGRATE_STATE=$T/migrate MAG_MIGRATE_MAGBOT=$BIN/magbot-native MAG_MIGRATE_MAGBOTCTL=$MAGBOT \
    MAG_MIGRATE_UNIT_DIR=$T/units MAG_BOTS_SITE=/nonexistent \
    MAG_MIGRATE_AGENT_CTL="$T/room-ctl" "$REPO/ops/migrate-legacy-bots.sh"
}
printf '#!/usr/bin/env bash\nMAG_BOTS_MAGBOTCTL=%q MAG_BOTS_MAGBOT=%q MAG_BOTS_SITE=/nonexistent MAGBOT_ENV_FILE=/nonexistent exec %q "$@"\n' \
  "$MAGBOT" "$BIN/magbot-native" "$REPO/ops/magbot-agent-ctl" >"$T/room-ctl"; chmod +x "$T/room-ctl"
legacy_gone() { [[ ! -e $MAGBOT_ROOT/bots/$1 && ! -e $T/units/magbot-$1.service ]]; }
# failure on bot 2: bot 1 migrated, bot 2 untouched, reported, no stamp
FAKE_NATIVE_FAIL=agent3 migrate >"$T/out" 2>&1 && fail "migration with failing bot 2 succeeded"
grep -q 'MIGRATION FAILED at agent3' "$T/out" || { cat "$T/out"; fail "failure not reported"; }
legacy_gone agent1 || fail "agent1 legacy left"
"$MAGBOT" verify agent1 | grep -q '^OK magbot-agent1 present' || fail "agent1 not migrated"
[[ -e $MAGBOT_ROOT/bots/agent3/metadata.json && -e $FAKE_STATE/gitea/magbot-agent3 ]] || fail "agent3 touched"
! grep -q 'virt-install magbot-agent3' "$FAKE_STATE/log" || fail "agent3 created despite failure"
[[ ! -e $T/migrate/done ]] || fail "stamp after failure"
# rerun resumes: agent1 kept as is, agent3 migrated
n1=$(grep -c '^virt-install magbot-agent1$' "$FAKE_STATE/log")
migrate >"$T/out" 2>&1 || { cat "$T/out"; fail "migration rerun"; }
[[ $(grep -c '^virt-install magbot-agent1$' "$FAKE_STATE/log") == "$n1" ]] || fail "agent1 re-created"
for id in agent1 agent3; do
  legacy_gone "$id" || fail "$id legacy left"
  "$MAGBOT" verify "$id" | grep -q "^OK magbot-$id present" || fail "$id not verified"
  grep -q '"state": "ready"' "$MAGBOT_ROOT/magbot-$id/metadata.json" || fail "$id not ready"
done
[[ -z $(ls -A "$MAGBOT_ROOT/bots") && -e $T/migrate/done && ! -e $T/migrate/agent3.pending ]] || fail "legacy registry or pending left"
# second run after the stamp is a no-op
cp "$FAKE_STATE/log" "$T/log.before"
migrate | grep -q 'already done' || fail "stamped rerun not a no-op"
cmp -s "$FAKE_STATE/log" "$T/log.before" || fail "stamped rerun touched something"
for id in agent1 agent3; do room_ctl despawn "$id" >/dev/null 2>&1 || fail "despawn migrated $id"; leak_check "$id"; done
echo "ok legacy migration"

[[ $(sha256sum "$GOLDEN") == "$GOLDEN_SUM" && $(stat -c %a "$GOLDEN") == 444 ]] || fail "golden modified"
[[ -z $(ls "$FAKE_STATE/domains") && -z $(ls "$FAKE_STATE/gitea") ]] || fail "global leak"
echo "magbot tests passed"
