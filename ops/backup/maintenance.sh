#!/usr/bin/env bash
set -euo pipefail
HERE=$(cd -- "$(dirname -- "$0")" && pwd)
source "$HERE/common.sh"
check_root
action="${1:-}"
case "$action" in retain|check|deep-check|restore-test|status|monitor) ;; *) echo 'Usage: maintenance.sh retain|check|deep-check|restore-test|status' >&2; exit 2 ;; esac
if [[ "$action" == monitor ]]; then
  now=$(date +%s)
  last=$(jq -r '.last_success // 0' "$MAG_BACKUP_STATUS" 2>/dev/null || echo 0)
  healthy=$(jq -r '.healthy // false' "$MAG_BACKUP_STATUS" 2>/dev/null || echo false)
  if (( now - last > 93600 )) || [[ "$healthy" != true ]]; then
    logger -p daemon.err -t mag-backup "ALERT: failed or older than 26 hours; last_success=$last" || :
    exit 1
  fi
  exit 0
fi
if [[ "$action" == status ]]; then
  [[ -f "$MAG_BACKUP_STATUS" ]] && jq --argjson now "$(date +%s)" \
    '. + {stale: ((.last_success // 0) < ($now - 93600)), now: $now}' "$MAG_BACKUP_STATUS" || echo '{"healthy":false,"stale":true}'
  exit 0
fi
backup_lock
tmp=''
finish() {
 local ec=$?
 trap - EXIT
 [[ -z "$tmp" ]] || rm -rf -- "$tmp"
 if (( ec != 0 )); then
  status_update --arg msg "$action failed, code $ec" --argjson at "$(date +%s)" \
   '.last_error=$msg | .last_failure=$at | .healthy=false' || :
  logger -p daemon.err -t mag-backup "FAILED maintenance $action code=$ec" 2>/dev/null || :
 fi
 exit "$ec"
}
trap finish EXIT
case "$action" in
  retain)
    # Never prune while S3 copying: all tasks share the same flock.
    restic forget --tag daily --group-by host,tags --keep-daily 7 --keep-weekly 4 --keep-monthly 6
    restic forget --tag hook-deploy --group-by host,tags --keep-within 14d
    restic forget --tag hook-clear --group-by host,tags --keep-within 14d
    restic prune
    ;;
  check|deep-check)
    if [[ "$action" == deep-check ]]; then restic check --read-data-subset=10%; else restic check; fi
    status_update --arg kind "$action" --argjson at "$(date +%s)" \
      '.last_check=$at | .last_check_kind=$kind | .last_check_ok=true | .last_error=""'
    ;;
  restore-test)
    tmp=$(mktemp -d "$MAG_BACKUP_WORK/restore-test.XXXXXXXX")
     "$HERE/restore.sh" --part all --target "$tmp/recovered" --no-lock
    # restore.sh deliberately materializes selected paths directly into
    # --target, not the original restic-internal .../payload/root wrapper.
    # Check the actual restored tree; never report an untested success.
    payload="$tmp/recovered"
    [[ -s "$payload/opt/mag-stack/backup-info.txt" && -d "$payload/var/lib/magbot/room" &&
       -s "$payload/opt/mag-stack/checksums.sha256" ]] || {
      echo "Restore test missing MAG payload or SHA256 manifest" >&2
      exit 1
    }
    # Independently check every restored payload file, not just restic blobs.
    (cd "$payload"; sha256sum --quiet -c opt/mag-stack/checksums.sha256)
    status_update --argjson at "$(date +%s)" \
      '.last_restore_test=$at | .last_restore_test_ok=true'
    ;;
esac
logger -t mag-backup "OK maintenance $action" 2>/dev/null || :
