#!/usr/bin/env bash
# Resize existing MAG bot VMs without recreating their disks or cloud-init.
# Run on the MAG host as root when the room is idle: sudo bash ops/vm/resize-existing-ram.sh
# The stopped guest retains the exact same libvirt domain and qcow2 overlay.
set -euo pipefail
target="${MAGBOT_TARGET_RAM_GIB:-16}"
[[ "$target" =~ ^[0-9]+$ ]] && (( target >= 8 && target <= 24 )) || {
  echo "MAG: target RAM must be 8..24 GiB" >&2; exit 64;
}
if (( EUID != 0 )); then echo "Run with sudo." >&2; exit 77; fi
command -v virsh >/dev/null
command -v python3 >/dev/null
command -v flock >/dev/null
command -v curl >/dev/null
root="${MAGBOT_ROOT:-/var/lib/libvirt/images/magbot}"
state_url="${MAGBOT_ROOM_STATE_URL:-http://127.0.0.1:8766/api/state}"
# Do not kill ongoing user work, pending commands, or background agents.
state=$(curl --fail --silent --show-error --connect-timeout 2 --max-time 6 "$state_url") || {
  echo "MAG: room state unavailable; refusing any VM restart." >&2; exit 75;
}
printf '%s' "$state" | python3 -c '
import sys,json
s=json.load(sys.stdin)
active=bool(s.get("busy") or s.get("busy_bots") or s.get("pending") or
           any(a.get("on") for a in s.get("activities",[]) if isinstance(a,dict)) or
           any(t.get("status")=="running" for t in s.get("tasks",[]) if isinstance(t,dict)))
if active:
    sys.exit("MAG: room has active or queued work; no VM memory changes.")
' || exit 75
exec 9>/run/lock/magbot.lock
flock -n 9 || { echo "MAG: VM lifecycle is busy; retry later." >&2; exit 75; }
backup_dir="/var/backups/mag-stack/vm-ram-$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$backup_dir"
chmod 0700 "$backup_dir"
vir() { virsh -c qemu:///system "$@"; }
for name in "${@:-agent1 agent2}"; do
  [[ "$name" =~ ^[a-z][a-z0-9-]{0,31}$ ]] || { echo "Invalid bot VM: $name" >&2; exit 64; }
  id="magbot-$name"
  meta="$root/bots/$name/metadata.json"
  [[ ! -L "$meta" && -f "$meta" ]] || { echo "Missing or unsafe registry: $meta" >&2; exit 74; }
  # Only managed libvirt domains may be updated.
  python3 - "$meta" "$id" <<'PY'
import json,sys
m=json.load(open(sys.argv[1]))
if m.get("id")!=sys.argv[2][7:] or m.get("domain")!=sys.argv[2]:
    sys.exit("MAG: VM registry ownership mismatch")
PY
  vir dominfo "$id" >/dev/null
  old=$(python3 -c 'import json,sys; print(int(json.load(open(sys.argv[1]))["ram_gib"]))' "$meta")
  if (( old >= target )); then echo "$id: $old GiB, no change."; continue; fi
  was_running=0
  if [[ "$(vir domstate "$id")" == running ]]; then was_running=1; fi
  vir dumpxml "$id" > "$backup_dir/$id.xml"
  cp -p -- "$meta" "$backup_dir/$id-metadata.json"
  if (( was_running )); then
    echo "$id: requesting graceful shutdown for RAM resize ($old -> $target GiB)"
    vir shutdown "$id"
    stopped=0
    for (( k=0; k<90; k++ )); do
      if [[ "$(vir domstate "$id")" == "shut off" ]]; then stopped=1; break; fi
      sleep 1
    done
    if (( !stopped )); then
      echo "$id: shutdown timed out; preserving the running VM unchanged." >&2
      exit 75
    fi
  fi
  # Never rebuild or delete the disk. A cold libvirt memory change keeps
  # vCPU topology, network identity, volumes and all VM files intact.
  if ! vir setmaxmem "$id" "${target}G" --config ||
     ! vir setmem "$id" "${target}G" --config; then
    echo "$id: libvirt update failed; restoring previous domain XML." >&2
    vir define "$backup_dir/$id.xml" || true
    if (( was_running )); then vir start "$id" || true; fi
    exit 1
  fi
  if (( was_running )); then
    if ! vir start "$id"; then
      echo "$id: start failed; restoring previous definition." >&2
      vir define "$backup_dir/$id.xml" || true
      vir start "$id" || true
      exit 1
    fi
  fi
  python3 - "$meta" "$target" <<'PY'
import json,sys,os,tempfile
path,target=sys.argv[1],int(sys.argv[2])
with open(path) as f: data=json.load(f)
data["ram_gib"]=target
folder=os.path.dirname(path)
fd,tmp=tempfile.mkstemp(prefix=".metadata-ram-",dir=folder)
try:
    st=os.stat(path)
    os.fchmod(fd,st.st_mode & 0o777)
    os.fchown(fd,st.st_uid,st.st_gid)
    with os.fdopen(fd,"w") as f:
        json.dump(data,f,separators=(",",":"))
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp,path)
finally:
    if os.path.exists(tmp): os.unlink(tmp)
PY
  echo "$id: RAM upgraded to $target GiB (disk preserved)."
done
echo "VM RAM migration finished; backups: $backup_dir"
