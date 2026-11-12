#!/usr/bin/env bash
# The whole of mag in one restic repository: everything needed to bring the
# system back on new hardware (system-restore.sh). Root; weekly
# (mag-backup-system.timer) and when Chris asks (settings, a bot's backup tool).
#
#   system.sh [--with-models]
#
# What goes in: the root file system (configuration, services, /usr/local,
# /opt, /var/lib with room, Gitea, Idol and scene jobs, /home with the venvs
# and runtimes, /root), the golden image, the archived VMs and the newest
# save of every bot VM (made fresh here: a running VM's disk is never copied
# as it is), and a description of the host in /var/lib/mag-backup/system-meta
# (packages, apt sources, disks, libvirt definitions, a consistent copy of
# Gitea's database, the model manifest). Models and weights go in only with
# --with-models and never into a repository on mag's own disk: they are
# downloaded again by their manifest (mag-modeld checks every SHA256).
#
# Where it goes, the first that applies:
#   MAG_SYSTEM_REPO from /etc/mag-backup/system.env (any restic repository;
#     a remote one needs RESTIC_PASSWORD_FILE there, it is encrypted with it),
#   a disk mounted at /mnt/mag-backup (repository mag-system on it),
#   /var/backups/mag-system on mag's own disk (protects against mistakes and
#     deletions, not against a broken disk; the last two states stay).
set -euo pipefail
umask 077
[[ $EUID == 0 ]] || { echo 'root required' >&2; exit 1; }
here=$(cd -- "$(dirname -- "$(readlink -f "$0")")" && pwd)
with_models=0
[[ ${1:-} == --with-models ]] && with_models=1
exec 9>/run/lock/mag-backup-system.lock
flock -n 9 || { echo 'A system backup is already running' >&2; exit 0; }
status=/var/lib/mag-backup/system-status.json
meta=/var/lib/mag-backup/system-meta
restic_bin=/usr/local/bin/restic
[[ -x $restic_bin ]] || restic_bin=$(command -v restic)
unset RESTIC_PASSWORD RESTIC_PASSWORD_COMMAND
MAG_SYSTEM_REPO=
if [[ -f /etc/mag-backup/system.env && ! -L /etc/mag-backup/system.env ]]; then
  # shellcheck disable=SC1091
  source /etc/mag-backup/system.env
fi
if [[ -n $MAG_SYSTEM_REPO ]]; then
  repo=$MAG_SYSTEM_REPO kind=configured
elif mountpoint -q /mnt/mag-backup; then
  repo=/mnt/mag-backup/mag-system kind=usb
else
  repo=/var/backups/mag-system kind=local
fi
export RESTIC_REPOSITORY=$repo
export GOMAXPROCS=${GOMAXPROCS:-8}
nopass=()
[[ -n ${RESTIC_PASSWORD_FILE:-} ]] || nopass=(--insecure-no-password)
r() { "$restic_bin" "${nopass[@]}" "$@"; }
started=$(date +%s)
fail() {
  jq -n --arg e "$1" --argjson at "$(date +%s)" --arg repo "$kind" \
    '{healthy:false, last_error:$e, last_failure:$at, target:$repo}' > "$status.tmp" &&
    { [[ -f $status ]] && jq -s '.[0] * .[1]' "$status" "$status.tmp" > "$status.new" && mv "$status.new" "$status" || mv "$status.tmp" "$status"; }
  rm -f "$status.tmp"
  logger -p daemon.err -t mag-backup-system "FAILED: $1" 2>/dev/null || :
  echo "system backup: $1" >&2
  exit 1
}
trap 'fail "aborted (exit $?)"' ERR
if [[ $kind != configured || $repo == /* ]]; then install -d -m 0700 "$repo"; fi
if (( with_models )) && [[ $kind == local ]]; then
  fail 'models are never copied onto mag'"'"'s own disk; connect a disk at /mnt/mag-backup or set MAG_SYSTEM_REPO'
fi
# On mag's own disk: room for the first full copy, and the models' downloads.
if [[ $kind == local ]]; then
  free=$(df -BG --output=avail /var/backups | tail -1 | tr -dc 0-9)
  need=$([[ -f $repo/config ]] && echo 60 || echo 170)
  (( free >= need )) || fail "only ${free} GiB free on mag's disk, need ${need} GiB; nothing written"
fi
[[ -f $repo/config ]] || r init >/dev/null 2>&1 || r cat config >/dev/null || fail "cannot open or create $repo"

# --------------------------------------------------- the host, described
rm -rf "$meta"
install -d -m 0700 "$meta"
dpkg --get-selections > "$meta/dpkg-selections.txt"
apt-mark showmanual > "$meta/apt-manual.txt" 2>/dev/null || :
snap list > "$meta/snaps.txt" 2>/dev/null || :
{ uname -a; lsb_release -ds 2>/dev/null; } > "$meta/system.txt"
lsblk -o NAME,SIZE,TYPE,FSTYPE,MOUNTPOINT,UUID > "$meta/disks.txt" 2>/dev/null || :
df -h -x tmpfs -x squashfs > "$meta/df.txt" 2>/dev/null || :
getent passwd > "$meta/passwd.txt"; getent group > "$meta/group.txt"
systemctl list-unit-files --no-legend > "$meta/unit-files.txt" 2>/dev/null || :
readlink /opt/mag-stack/current > "$meta/current-release.txt" 2>/dev/null || :
if command -v virsh >/dev/null; then
  install -d -m 0700 "$meta/libvirt"
  for d in $(virsh -q list --all --name | grep '^magbot-' || true); do virsh dumpxml --inactive "$d" > "$meta/libvirt/$d.xml"; done
  for n in $(virsh -q net-list --all --name); do virsh net-dumpxml "$n" > "$meta/libvirt/net-$n.xml"; done
  virsh -q list --all --autostart --name > "$meta/libvirt/autostart.txt" || :
fi
if [[ -f /var/lib/gitea/data/gitea.db ]]; then
  sqlite3 /var/lib/gitea/data/gitea.db ".timeout 10000" ".backup '$meta/gitea.db'"
fi
# Models: what they are and where they come from (no weights).
cp "$here/../models/models.json" "$meta/models.json" 2>/dev/null || :
find /home/chris/models /home/chris/idol-mi50/weights -type f -printf '%s\t%p\n' 2>/dev/null > "$meta/model-files.txt" || :

# --------------------------------------------------- a fresh save of each VM
# A running VM's disk is never copied as it is; its newest save is.
/usr/local/bin/magbot save-all system || echo 'system backup: a VM save failed; its older saves go in' >&2

# --------------------------------------------------- the file system
excludes=$(mktemp)
cat > "$excludes" <<EOF
/proc
/sys
/dev
/run
/tmp
/var/tmp
/mnt
/media
/lost+found
/swap.img
/var/cache
/var/lib/snapd/snaps
/var/lib/docker
/var/backups/mag-system
/var/backups/mag-full
/var/backups/mag-restic
/var/lib/mag-backup/work
/var/lib/libvirt/images/magbot/bots/*/root.qcow2
/var/lib/libvirt/images/magbot/bots/*/root.save-overlay.qcow2
/var/lib/libvirt/images/magbot/bots/*/root.qcow2.new
/home/*/.cache
/root/.cache
/var/lib/mag-idol/tmp
EOF
if (( ! with_models )); then
  printf '%s\n' /home/chris/models /home/chris/idol-mi50/weights '*.gguf' >> "$excludes"
fi
trap 'rm -f "$excludes"; fail "aborted (exit $?)"' ERR
tags=(--tag system)
(( ! with_models )) || tags+=(--tag models)
r backup --one-file-system --exclude-caches --exclude-file "$excludes" --host mag \
  "${tags[@]}" / --json > "$meta.summary" 2>/dev/null ||
  { rc=$?; (( rc == 3 )) || fail "restic backup failed (exit $rc)"; } # 3: some files unreadable, snapshot written
rm -f "$excludes"
summary=$(jq -sc '[.[] | select(.message_type=="summary")][-1] // {}' "$meta.summary")
rm -f "$meta.summary"
snapshot=$(jq -r '.snapshot_id // empty' <<<"$summary")
[[ -n $snapshot ]] || fail 'no snapshot id'
# How many states stay: two on mag's own disk, a history elsewhere.
if [[ $kind == local ]]; then
  r forget --host mag --tag system --keep-last 2 --prune >/dev/null
else
  r forget --host mag --tag system --keep-weekly 6 --keep-monthly 6 --prune >/dev/null
fi
r check >/dev/null || fail 'restic check failed after the backup'
trap - ERR
jq -n --arg id "$snapshot" --arg repo "$kind" --argjson at "$(date +%s)" \
  --argjson seconds "$(( $(date +%s) - started ))" --argjson summary "$summary" --argjson models "$with_models" \
  '{healthy:true, last_success:$at, last_snapshot:$id, target:$repo, seconds:$seconds, with_models:($models==1),
    added_bytes:($summary.data_added // 0), total_bytes:($summary.total_bytes_processed // 0), last_error:""}' > "$status"
logger -t mag-backup-system "OK snapshot=$snapshot target=$kind" 2>/dev/null || :
echo "system backup: snapshot $snapshot in $kind repository"
