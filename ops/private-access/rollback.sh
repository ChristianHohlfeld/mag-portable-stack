#!/usr/bin/env bash
# Restore nginx vhost, link, host ACME scripts and systemd units as one cutover.
# Cleanup happens ONLY after nginx -t AND reload of the restored config succeed.
set -euo pipefail
[[ $EUID == 0 ]] || exit 1
site=/etc/nginx/sites-available/mag-room.conf
backup=$site.pre-whois
enabled=/etc/nginx/sites-enabled/mag-room.conf
state=/var/lib/mag-private-cutover
host_files=(
 /usr/local/lib/mag-private/rollback.sh
 /usr/local/lib/mag-private/confirm.sh
 /usr/local/lib/mag-acme/issue-cert.sh
 /usr/local/lib/mag-acme/deploy-hook.sh
 /etc/systemd/system/mag-acme-renew.service
 /etc/systemd/system/mag-acme-renew.timer
)
[[ -s $backup && ! -L $backup && -s $state/enabled.before ]] ||
 { echo "No complete original nginx backup; inspect manually" >&2; exit 1; }
original=$(cat "$state/enabled.before")
[[ $original == ABSENT || $original == /* || $original == ../* ]] ||
 { echo "Invalid enabled symlink backup" >&2; exit 1; }
# cp -a + rename restores ownership, permissions and timestamps, including
# the original 0644 mode. Never chmod the backup itself.
cp -a -- "$backup" "$site.rollback"
mv -f -- "$site.rollback" "$site"
if [[ $original == ABSENT ]]; then
  rm -f -- "$enabled"
else
  ln -sfn -- "$original" "$enabled"
fi

# host.ready proves all six snapshots existed BEFORE the installer replaced
# anything. On a failed snapshot (before mutations), leave host files alone.
if [[ -e $state/host.ready ]]; then
  for i in "${!host_files[@]}"; do
    f=${host_files[$i]}
    if [[ -e $state/host/$i || -L $state/host/$i ]]; then
      rm -f -- "$f"
      cp -a -- "$state/host/$i" "$f"
    elif [[ -e $state/host/$i.absent ]]; then
      rm -f -- "$f"
    else
      echo "Host file snapshot $i corrupt; manual recovery required" >&2
      exit 1
    fi
  done
  systemctl daemon-reload
fi
nginx -t
systemctl reload nginx.service
systemctl stop mag-private-rollback.timer 2>/dev/null || true

# Successful recovery: no lingering .pre-whois or enabled.before blockers.
rm -f -- "$backup" "$state/enabled.before" "$state/pending" "$state/confirmed" \
  "$state/host.ready" "$state/nginx.dump" "$site.new" "$site.rollback"
rm -rf -- "$state/host"
echo "Original nginx/ACME state and file mode restored; retry safe. mag-llm untouched."
