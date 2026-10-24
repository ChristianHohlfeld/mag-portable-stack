#!/usr/bin/env bash
# Never modifies a live MAG service without explicit --apply.
set -euo pipefail
HERE=$(cd -- "$(dirname -- "$0")" && pwd)
source "$HERE/common.sh"
check_root
part=all; target=''; apply=0; snapshot=latest; repo=local; use_lock=1
while (( $# )); do
 case "$1" in
 --part) part="$2"; shift 2;;
 --target) target="$2"; shift 2;;
 --snapshot) snapshot="$2"; shift 2;;
 --repo) repo="$2"; shift 2;;
 --apply) apply=1; shift;;
 --no-lock) use_lock=0; shift;;
 *) echo "Unknown argument $1" >&2; exit 2;;
 esac
done
case "$part" in room|gitea|pages|cron|tailscale|config|all) ;; *) exit 2;; esac
case "$repo" in local) ;; offsite) echo 'S3 offsite was removed; only --repo local exists' >&2; exit 2;; *) exit 2;; esac
[[ "$snapshot" == latest || "$snapshot" =~ ^[a-f0-9]{8,64}$ || "$snapshot" =~ ^[0-9]{8}T[0-9]+Z-(daily|deploy|clear)$ ]] || exit 2
[[ $use_lock == 0 ]] || backup_lock
# Never restore to / without an explicit apply; staging is mandatory.
if [[ $apply == 0 ]]; then
 [[ -n "$target" && "$target" == /* && "$target" != / && ! -L "$target" ]] || { echo 'Absolute --target required' >&2; exit 2; }
else
 [[ $EUID == 0 && "$target" == "" ]] || { echo 'Live apply requires root and no --target' >&2; exit 2; }
fi
scratch=$(mktemp -d "$MAG_BACKUP_WORK/restore.XXXXXXXX")
trap 'rm -rf -- "$scratch"' EXIT
if [[ "$repo" == local ]]; then
  root="${MAG_BACKUP_ROOT:-/var/backups/mag-full}"
  if [[ ! -f "${RESTIC_REPOSITORY:-}/config" ]]; then
    chosen=""
    if [[ "$snapshot" != latest && -d "$root/$snapshot/repo" && -f "$root/$snapshot/VERIFIED" ]]; then
      chosen="$root/$snapshot"
      snapshot=latest
    else
      while IFS= read -r id; do
        [[ -f "$root/$id/VERIFIED" && -d "$root/$id/repo" ]] || continue
        chosen="$root/$id"
      done < <(find "$root" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' 2>/dev/null | LC_ALL=C sort)
    fi
    [[ -n "$chosen" ]] || { echo 'No verified full backup' >&2; exit 1; }
    export RESTIC_REPOSITORY="$chosen/repo"
    echo "Restoring independent archive $(basename "$chosen")"
  fi
fi
mkdir -p "$scratch/recovered"
restic restore "$snapshot" --target "$scratch/recovered"
payload=$(find "$scratch/recovered" -type d -name root -path '*/payload/root' -print -quit)
[[ -n "$payload" && -d "$payload/var/lib/magbot/room" ]] || { echo 'No valid MAG payload' >&2; exit 1; }
paths=()
case "$part" in
 room) paths=(var/lib/magbot/room);;
 gitea) paths=(var/lib/gitea etc/gitea);;
 pages) paths=(var/lib/mag-pages etc/mag-pages);;
 cron) paths=(etc/crontab etc/cron.d etc/cron.hourly etc/cron.daily etc/cron.weekly etc/cron.monthly var/spool/cron/crontabs);;
 tailscale) paths=(var/lib/tailscale);;
 config) paths=(etc/mag-acme etc/mag-nginx etc/nginx etc/magbot etc/default/mag-room etc/mag-stack etc/credstore/mag-guest-oauth etc/credstore/mag-secrets-key etc/mag-audit var/lib/mag-audit etc/systemd/system var/lib/mag-deploy var/lib/mag-private-cutover var/lib/mag-llm opt/mag-stack etc/crontab etc/cron.d etc/cron.hourly etc/cron.daily etc/cron.weekly etc/cron.monthly var/spool/cron/crontabs);;
 all) paths=(var/lib/magbot/room var/lib/gitea etc/gitea var/lib/mag-pages etc/mag-pages etc/mag-acme etc/mag-nginx etc/nginx etc/magbot etc/default/mag-room etc/mag-stack etc/credstore/mag-guest-oauth etc/credstore/mag-secrets-key etc/mag-audit var/lib/mag-audit etc/systemd/system var/lib/mag-deploy var/lib/mag-private-cutover var/lib/mag-llm opt/mag-stack etc/crontab etc/cron.d etc/cron.hourly etc/cron.daily etc/cron.weekly etc/cron.monthly var/spool/cron/crontabs var/lib/tailscale);;
esac
destination="${target:-/}"
# Preflight EVERY service prerequisite before copying ANY live path, so a
# mixed 'all' restore can never half-apply then discover a running service.
if [[ $apply == 1 ]]; then
  case "$part" in
    room|all)
      if systemctl is-active --quiet mag-120.service 2>/dev/null; then
        echo 'Stop mag-120 before live restore' >&2; exit 1
      fi ;;
  esac
  case "$part" in
    gitea|all)
      if systemctl is-active --quiet gitea.service 2>/dev/null; then
        echo 'Stop Gitea before live restore' >&2; exit 1
      fi ;;
  esac
  case "$part" in
    tailscale|all)
      if systemctl is-active --quiet tailscaled.service 2>/dev/null; then
        echo 'Stop tailscaled before live restore; never duplicate the Node key' >&2; exit 1
      fi ;;
  esac
fi
for path in "${paths[@]}"; do
 [[ -e "$payload/$path" || -L "$payload/$path" ]] || continue
 if [[ $apply == 1 ]]; then
   if [[ "$path" == var/lib/tailscale ]] && systemctl is-active --quiet tailscaled.service 2>/dev/null; then
     echo 'Stop tailscaled before --apply tailscale/all; do not reuse live Node keys' >&2; exit 1
   fi
   # Gitea live SQLite database requires Gitea stopped by the operator.
   if [[ "$path" == var/lib/gitea && $(systemctl is-active gitea.service 2>/dev/null || :) == active ]]; then
     echo 'Stop Gitea before --apply gitea/all' >&2; exit 1
   fi
   if [[ "$path" == var/lib/magbot/room && $(systemctl is-active mag-120.service 2>/dev/null || :) == active ]]; then
     echo 'Stop mag-120 before --apply room/all' >&2; exit 1
   fi
 fi
 mkdir -p "$destination/$(dirname "$path")"
 if [[ -d "$payload/$path" && ! -L "$payload/$path" ]]; then
   mkdir -p "$destination/$path"
   cp -a -- "$payload/$path/." "$destination/$path/"
 else
   cp -a -- "$payload/$path" "$destination/$path"
 fi
done
printf 'Restored snapshot=%s part=%s into %s (live=%s)\n' "$snapshot" "$part" "$destination" "$apply"
