#!/usr/bin/env bash
# One-shot migration of the legacy room bots (agent1, agent3) to the magbot
# CLI. Per bot: legacy teardown via magbot-native (registry, overlay, unit,
# nft, Gitea user), then a fresh CLI bot with the same name (workspace and
# guest memory wiped, owner-approved), then verify. The room keeps its
# vm=agentN references. A failure stops the run and leaves later bots
# untouched; a rerun resumes at the failed bot (pending marker). The stamp
# makes later deploys a no-op. mag-llm is never touched.
set -uo pipefail
ROOT=${MAGBOT_ROOT:-/var/lib/libvirt/images/magbot}
STATE=${MAG_MIGRATE_STATE:-/var/lib/mag-stack/migrate-legacy}
STAMP=$STATE/done
NATIVE=${MAG_MIGRATE_MAGBOT:-/usr/local/bin/magbot}
CTL=${MAG_MIGRATE_AGENT_CTL:-/usr/local/sbin/magbot-agent-ctl}
MAGBOTCTL=${MAG_MIGRATE_MAGBOTCTL:-/usr/local/bin/magbotctl}
SITE=${MAG_BOTS_SITE:-/usr/local/lib/mag-bots/site.sh}
UNIT_DIR=${MAG_MIGRATE_UNIT_DIR:-/etc/systemd/system}
VIRSH=${MAG_MIGRATE_VIRSH:-virsh}
IDS=${MAG_MIGRATE_IDS:-agent1 agent3}

if [[ -e $STAMP ]]; then echo "legacy migration already done ($(cat "$STAMP"))"; exit 0; fi
mkdir -p -- "$STATE" || exit 1

legacy_present() { # registry entry, native unit, or a domain without CLI metadata
  local id=$1
  [[ -e $ROOT/bots/$id || -e $UNIT_DIR/magbot-$id.service ]] && return 0
  [[ ! -e $ROOT/magbot-$id/metadata.json ]] && "$VIRSH" -c qemu:///system dominfo "magbot-$id" >/dev/null 2>&1
}
abort() { echo "MIGRATION FAILED at $1: $2; later bots untouched, rerun resumes here" >&2; exit 1; }

for id in $IDS; do
  pending=$STATE/$id.pending
  if ! legacy_present "$id" && [[ ! -e $pending ]]; then
    if [[ -e $ROOT/magbot-$id/metadata.json ]]; then echo "$id: already a magbot bot"; else echo "$id: absent, nothing to migrate"; fi
    continue
  fi
  echo "$id: migrating"
  : >"$pending" || abort "$id" "cannot write $pending"
  if legacy_present "$id"; then
    "$NATIVE" despawn "$id" || abort "$id" "legacy teardown failed (legacy bot kept)"
    if [[ -x $SITE ]]; then "$SITE" remove "$id" || echo "WARN: bot site cleanup failed for $id" >&2; fi
    legacy_present "$id" && abort "$id" "legacy leftovers after teardown"
  fi
  # Room entry point: magbotctl create + audit proxy + bot site.
  "$CTL" create "$id" || abort "$id" "magbotctl create failed (rolled back; no VM)"
  "$MAGBOTCTL" verify "$id" || abort "$id" "verify failed"
  rm -f -- "$pending"
  echo "$id: migrated"
done
date -u +%FT%TZ >"$STAMP" || exit 1
echo "legacy migration done"
