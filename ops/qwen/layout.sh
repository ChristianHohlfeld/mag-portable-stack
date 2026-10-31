#!/usr/bin/env bash
# Transactional GLM layout switch: avoid evicting warm NUMA model units.
set -euo pipefail
ROOT=$(cd -- "$(dirname -- "$(readlink -f "$0")")/../.." && pwd)
# shellcheck source=../mag-units.sh
source "$ROOT/ops/mag-units.sh"
ACTION=${1:?apply|rollback|probe}; BACKUP=${2:?deployment backup directory}
TXN="$BACKUP/qwen-layout"
fail(){ echo "NUMA1 layout: $*" >&2; exit 4; }
[[ $EUID == 0 ]] || fail 'root required'
units=(mag-llm-ds-node@1.service mag-llm-qwen.service mag-llm-qwen-room.service mag-llm-glm.service mag-llm-glm@q1.service mag-llm-glm@q2.service)
[[ ! -f "$TXN/glm-dual" ]] || units+=(mag-llm-ds-node@0.service mag-llm-ornith-node@0.service mag-llm-ornith@1.service)
files=(/etc/mag-stack/room-model.env /etc/mag-stack/room-layout /etc/mag-stack/room-layout.pinned
       /etc/mag-qwen/runtime.env
       /etc/systemd/system/mag-llm-qwen.service /etc/systemd/system/mag-llm-qwen-room.service /etc/systemd/system/mag-llm-ds-node@.service
       /usr/local/lib/mag-qwen/start.sh /usr/local/lib/mag-qwen/verify-model.sh
       /usr/local/lib/mag-qwen/runtime.sha /etc/mag-qwen/model.sha256
       /etc/systemd/system/mag-audit-gateway.service.d/50-two-bots.conf)
# A failed attempt of the repo-declared layout is remembered per identity so the
# auto-update timer does not stop/restart NUMA1 on every tick (see select below).
FAILED=${MAG_LAYOUT_FAILED_FILE:-/var/lib/mag-stack/layout-failed}
disable_if_installed(){
  # A pure-DeepSeek host may never have had a Qwen unit file.
  if systemctl cat "$1" >/dev/null 2>&1; then systemctl disable "$1"; fi
}
# A rebuilt runtime is published with mv over the old binary: a resident model
# keeps running the replaced (now deleted) executable until it is restarted.
runs_stale_binary(){
  local pid
  pid=$(systemctl show -p MainPID --value "$1" 2>/dev/null || true)
  [[ $pid =~ ^[1-9][0-9]*$ ]] || return 1
  [[ $(readlink "/proc/$pid/exe" 2>/dev/null || true) == *' (deleted)' ]]
}
rollback(){
  [[ -f $TXN/started && ! -f $TXN/restored ]] || return 0
  # Roll back only Qwen/Q1 room cutover units; never stop the resident GLM-Q2.
  if [[ -f $TXN/changed ]]; then
    if [[ -f $TXN/glm-dual ]]; then
      if [[ -f $TXN/restarted ]]; then
        while IFS= read -r unit; do
          [[ -z $unit ]] || systemctl stop "$unit"
        done < "$TXN/restarted"
      fi
    else
      systemctl stop "${units[@]}"
    fi
  fi
   if [[ ! -f $TXN/glm-dual && -f $TXN/hidden-dropins ]]; then
     while IFS= read -r f; do [[ ! -f "$f.glm-inactive" ]] || mv -f -- "$f.glm-inactive" "$f"; done < "$TXN/hidden-dropins"
   fi
  for unit in "${units[@]}"; do
    [[ ! -f $TXN/glm-dual || $unit != mag-llm-glm@q2.service ]] || continue
    disable_if_installed "$unit"
  done
  for file in "${files[@]}"; do
    if [[ -f $TXN/files$file ]]; then
      install -D -m "$(stat -c %a "$TXN/files$file")" "$TXN/files$file" "$file"
    else rm -f "$file"; fi
  done
  if [[ -f $TXN/glm-dual ]]; then
    # Remove legacy gateway mapping BEFORE systemd reload, not afterwards.
    rm -f /etc/systemd/system/mag-audit-gateway.service.d/50-two-bots.conf
  fi
  MAG_DEFER_DAEMON_RELOAD=0 mag_flush_daemon_reload --force
  for unit in "${units[@]}"; do
    if [[ -f $TXN/glm-dual ]]; then
      case "$unit" in
        mag-llm-glm@q2.service) continue ;;
        mag-llm-ds-node@*.service|mag-llm-ornith*)
          disable_if_installed "$unit"; continue ;;
      esac
    fi
    if grep -Fxq "$unit" "$TXN/enabled"; then systemctl enable "$unit"; fi
    if [[ -f $TXN/changed ]] && grep -Fxq "$unit" "$TXN/active"; then
      if [[ ! -f $TXN/glm-dual ]] || grep -Fxq "$unit" "$TXN/restarted"; then
        systemctl start "$unit"
      fi
    fi
  done
  touch "$TXN/restored"
  # The rollback is complete before we probe the restored Qwen. It now uses
  # the pinned shared runtime built for this release. A smoke failure must
  # NEVER abort or undo the rollback: signal the degraded state visibly.
  if [[ -f $TXN/changed ]] &&
     grep -Fxq mag-llm-qwen-room.service "$TXN/active"; then
    qwen_ready=0
    for ((qi=0; qi<120; qi++)); do
      if systemctl is-active --quiet mag-llm-qwen-room.service &&
         curl --noproxy '*' -fsS --max-time 3 -o /dev/null http://127.0.0.1:8191/health 2>/dev/null; then
        qwen_ready=1
        break
      fi
      sleep 5
    done
    if (( qwen_ready )); then
      if python3 -I "$ROOT/ops/qwen/smoke.py" --port 8191 \
          --model qwen3.8-flash-next-uncensored --quick; then
        echo 'QWEN ROLLBACK SMOKE PASS (new pinned runtime)'
      else
        echo 'WARNING: Qwen restored and healthy, but rollback smoke failed' >&2
      fi
    else
      echo 'WARNING: Qwen rollback service restored, health still unavailable' >&2
    fi
  fi
}
if [[ $ACTION == rollback ]]; then rollback; exit; fi
[[ $ACTION == apply || $ACTION == probe ]] || fail 'unknown action'
# Layout source, highest first:
#  1. MAG_ROOM_LAYOUT=mixed|deepseek|glm  explicit operator choice; it is pinned
#     on the host and survives later automatic updates (MAG_ROOM_LAYOUT=repo unpins).
#  2. the pinned host choice (/etc/mag-stack/room-layout.pinned)
#  3. the repository's ops/room/layout (normal case: the stack follows the repo)
#  4. the last applied host layout, then mixed
valid(){ [[ $1 == mixed || $1 == deepseek || $1 == glm ]]; }
previous=$(cat /etc/mag-stack/room-layout 2>/dev/null || echo mixed)
valid "$previous" || previous=mixed
pin=''
if [[ -n ${MAG_ROOM_LAYOUT:-} && ${MAG_ROOM_LAYOUT} != repo ]]; then
  layout=$MAG_ROOM_LAYOUT; pin=$layout; source=operator
elif [[ -z ${MAG_ROOM_LAYOUT:-} && -s /etc/mag-stack/room-layout.pinned ]]; then
  layout=$(cat /etc/mag-stack/room-layout.pinned); pin=$layout; source=pinned
elif [[ -s $ROOT/ops/room/layout ]]; then
  layout=$(tr -d '[:space:]' < "$ROOT/ops/room/layout"); source=repo
else
  layout=$previous; source=host
fi
valid "$layout" || fail 'MAG_ROOM_LAYOUT must be mixed, deepseek or glm'
# Identity of the repo-declared target: runtime + every file that defines it.
# One failed live attempt per identity; a new commit touching them retries.
case $layout in mixed) env_file=mixed.env ;; glm) env_file=glm.env ;; *) env_file=model.env ;; esac
id_files=("$ROOT/ops/qwen/runtime.sha" "$ROOT/ops/room/$env_file")
if [[ $layout == glm ]]; then id_files+=("$ROOT/ops/qwen/start.sh" "$ROOT/ops/qwen/mag-llm-qwen-room.service" "$ROOT/ops/qwen/install.sh"); fi
identity="$layout $(cat "${id_files[@]}" 2>/dev/null | sha256sum | cut -c1-16)"
# The probe is read-only and runs BEFORE the deploy's backup and service staging.
# Never turn a failed desired layout into a successful deploy of the old one.
if [[ $source == repo && -f $FAILED && $(cat "$FAILED") == "$identity" ]]; then
  echo "NUMA1 layout blocked: repo layout '$layout' previously failed live ($identity); previous '$previous' retained" >&2
  if [[ $ACTION == probe ]]; then exit 78; fi
  fail "desired layout '$layout' has a matching prior failure; refusing to claim deployment success"
fi
[[ $ACTION != probe ]] || exit 0
# Fresh bootstrap has no Qwen download. Keep its existing DeepSeek provisioning.
[[ -f /etc/mag-stack/room-model.env ]] || exit 0
if [[ $layout == mixed ]]; then
  REV=$(cat "$ROOT/ops/qwen/runtime.sha")
  BIN="/home/chris/.local/qwen4exp/$REV-mag-cp1/bin/llama-server"
  patch_id=$(sha256sum "$ROOT/ops/qwen/end-checkpoint.patch" "$ROOT/ops/glm/arch-compat.patch" | sha256sum | cut -d' ' -f1)
  if [[ ! -x $BIN || $(cat "$(dirname "$BIN")/source-commit" 2>/dev/null || true) != "$REV" ||
        $(cat "$(dirname "$BIN")/patch-identity" 2>/dev/null || true) != "$patch_id" ]]; then
    runuser -u chris -- env HOME=/home/chris nice -n 19 ionice -c3 bash "$ROOT/ops/qwen/build-runtime.sh"
  fi
fi
if [[ $layout == mixed || $layout == glm ]]; then
  [[ -s /home/chris/models/qwen3.8-flash-next-uncensored/upstream.sha256 ]] || fail 'download/upstream.sha256 missing'
fi
install -d -m 0700 "$TXN"
[[ ! -e $TXN/started ]] || fail 'transaction already exists'
if [[ $layout == glm ]]; then
  # Q2 is a live invariant: no staging, restart, or fallback may touch it.
  q2_pid=$(systemctl show -p MainPID --value mag-llm-glm@q2.service 2>/dev/null || true)
  [[ $q2_pid =~ ^[1-9][0-9]*$ ]] || fail 'GLM-Q2 must already be running; refusing to start/restart it'
  systemctl is-enabled --quiet mag-llm-glm@q2.service || fail 'GLM-Q2 must already be enabled; refusing to change its boot state'
  curl --noproxy '*' -fsS --max-time 5 -o /dev/null http://127.0.0.1:8192/health || fail 'GLM-Q2 :8192 is not healthy'
  REV=$(cat "$ROOT/ops/qwen/runtime.sha")
  BIN="/home/chris/.local/qwen4exp/$REV-mag-cp1/bin/llama-server"
  patch_id=$(sha256sum "$ROOT/ops/qwen/end-checkpoint.patch" "$ROOT/ops/glm/arch-compat.patch" | sha256sum | cut -d' ' -f1)
  [[ -x $BIN && $(cat "$(dirname "$BIN")/source-commit" 2>/dev/null || true) == "$REV" &&
     $(cat "$(dirname "$BIN")/patch-identity" 2>/dev/null || true) == "$patch_id" ]] || fail 'Qwen runtime must already match GLM-Q2; refusing shared runtime rebuild'
  touch "$TXN/glm-dual"
  units+=(mag-llm-ds-node@0.service mag-llm-ornith-node@0.service mag-llm-ornith@1.service)
fi
: > "$TXN/active"; : > "$TXN/enabled"
for unit in "${units[@]}"; do
  if systemctl is-active --quiet "$unit"; then echo "$unit" >> "$TXN/active"; fi
  if systemctl is-enabled --quiet "$unit"; then echo "$unit" >> "$TXN/enabled"; fi
done
for file in "${files[@]}"; do
  if [[ -f $file ]]; then install -D -m "$(stat -c %a "$file")" "$file" "$TXN/files$file"; fi
done
install -m 0700 "$0" "$TXN/helper.sh"
# The copy is later executed as $BACKUP/qwen-layout/helper.sh. Its own
# dirname($0)/../.. is the backup parent (/var/backups/mag-stack), so
# `source "$ROOT/ops/mag-units.sh"` would miss the stack. Pin the root
# this process already resolved.
python3 - "$TXN/helper.sh" "$ROOT" <<'PY'
import pathlib, sys
path, root = sys.argv[1], sys.argv[2]
file = pathlib.Path(path)
lines = file.read_text().splitlines(keepends=True)
out, done = [], False
for line in lines:
    if not done and line.startswith("ROOT="):
        out.append("ROOT='%s'\n" % root.replace("'", "'\\''"))
        done = True
    else:
        out.append(line)
if not done:
    raise SystemExit("layout helper: ROOT= line missing")
file.write_text("".join(out))
PY
touch "$TXN/started"
on_exit(){
  local rc=$?; trap - EXIT
  if (( rc )); then
    # Remember only a failed switch to a new layout, never an explicit pin.
    if [[ -z $pin && $source == repo ]]; then
      install -d -m 0755 "$(dirname "$FAILED")" && printf '%s\n' "$identity" > "$FAILED" || true
    fi
    rollback
  fi
  exit "$rc"
}
trap on_exit EXIT
# Stage/verify the existing 98 GB Qwen GGUF before retiring Q1.
# Do not run the GLM installer: GLM-Q2's unit, env, launcher and flags stay untouched.
if [[ $layout == mixed || $layout == glm ]]; then bash "$ROOT/ops/qwen/install.sh" refresh; fi
# The room-only Qwen unit conflicts with Q1, not Q2; the Q2 PID is guarded below.
install -m 0644 "$ROOT/ops/room/mag-llm-ds-node@.service" /etc/systemd/system/mag-llm-ds-node@.service
install -d -m 0755 /etc/mag-stack /etc/mag-qwen
case $layout in
  mixed) selected=mag-llm-qwen.service; config=mixed.env
         printf 'QWEN_NODE=1\nQWEN_PORT=8192\nQWEN_THREADS=14\nQWEN_THREADS_BATCH=14\nQWEN_MIN_RAM_GIB=150\n' > /etc/mag-qwen/runtime.env ;;
  glm)   selected=mag-llm-qwen-room.service; config=glm.env
         printf 'QWEN_NODE=0\nQWEN_PORT=8191\nQWEN_THREADS=14\nQWEN_THREADS_BATCH=28\nQWEN_MIN_RAM_GIB=150\n' > /etc/mag-qwen/runtime.env ;;
  *)     selected=mag-llm-ds-node@1.service; config=model.env ;;
esac
selected_units=("$selected")
if [[ $layout == glm ]]; then selected_units=(mag-llm-qwen-room.service mag-llm-glm@q2.service); fi
others=()
for unit in "${units[@]}"; do
  keep=0
  for chosen in "${selected_units[@]}"; do [[ $unit != "$chosen" ]] || keep=1; done
  (( keep )) || others+=("$unit")
done
# Qwen may be started/restarted on NUMA0; GLM-Q2 is a strict no-touch invariant.
change=0
declare -A restart_unit=()
for unit in "${selected_units[@]}"; do
  if ! systemctl is-active --quiet "$unit"; then
    restart_unit["$unit"]=1
    change=1
  fi
done
if [[ $layout == glm ]]; then
  if ! systemctl is-active --quiet mag-llm-glm@q2.service ||
     runs_stale_binary mag-llm-glm@q2.service; then
    fail 'GLM-Q2 is not in its original live state; refusing any Q2 restart'
  fi
  qwen_changed=0
  for file in /usr/local/lib/mag-qwen/start.sh \
      /etc/systemd/system/mag-llm-qwen-room.service \
      /usr/local/lib/mag-qwen/runtime.sha /etc/mag-qwen/runtime.env; do
    cmp -s "$file" "$TXN/files$file" || qwen_changed=1
  done
  if (( qwen_changed )) || runs_stale_binary mag-llm-qwen-room.service; then
    restart_unit[mag-llm-qwen-room.service]=1
    change=1
  fi
else
  if [[ $layout == mixed ]]; then
    for file in /usr/local/lib/mag-qwen/start.sh /etc/systemd/system/mag-llm-qwen.service /etc/mag-qwen/runtime.env; do
      cmp -s "$file" "$TXN/files$file" || change=1
    done
  fi
  if runs_stale_binary "$selected"; then change=1; fi
fi
install -m 0644 "$ROOT/ops/room/$config" /etc/mag-stack/room-model.env
printf '%s\n' "$layout" > /etc/mag-stack/room-layout
if [[ -n $pin ]]; then printf '%s\n' "$pin" > /etc/mag-stack/room-layout.pinned
elif [[ ${MAG_ROOM_LAYOUT:-} == repo ]]; then rm -f /etc/mag-stack/room-layout.pinned; fi
# Avoid stale :8191 owners and the legacy Ornith gateway override.
# Moved .conf files are outside systemd's drop-in glob; rollback restores them.
if [[ $layout == glm ]]; then
  rm -f /etc/systemd/system/mag-audit-gateway.service.d/50-two-bots.conf
  : > "$TXN/hidden-dropins"
  for legacy in mag-llm-ds-node@0.service mag-llm-ornith-node@0.service mag-llm-ornith@1.service; do
    dir="/etc/systemd/system/$legacy.d"
    [[ -d $dir ]] || continue
    for f in "$dir"/*.conf; do
      [[ -f $f ]] || continue
      if grep -Eq '(^|[^0-9])8191([^0-9]|$)' "$f"; then
        mv -f -- "$f" "$f.glm-inactive"
        printf '%s\n' "$f" >> "$TXN/hidden-dropins"
      fi
    done
  done
fi
mag_daemon_reload
mag_flush_daemon_reload
for unit in "${others[@]}"; do
  if systemctl is-active --quiet "$unit"; then change=1; fi
done
if (( change )); then
  touch "$TXN/changed"
  : > "$TXN/restarted"
  for unit in "${others[@]}"; do
    if systemctl is-active --quiet "$unit"; then
      printf '%s\n' "$unit" >> "$TXN/restarted"
      systemctl stop "$unit"
    fi
  done
  for unit in "${selected_units[@]}"; do
    if [[ $layout != glm || -n ${restart_unit[$unit]:-} ]]; then
      # Write ahead: a failure during stop/start must remain reversible.
      printf '%s\n' "$unit" >> "$TXN/restarted"
      if systemctl is-active --quiet "$unit"; then systemctl stop "$unit"; fi
    fi
  done
fi
for unit in "${others[@]}"; do disable_if_installed "$unit"; done
for unit in "${selected_units[@]}"; do
    [[ $layout != glm || $unit != mag-llm-glm@q2.service ]] || continue
    systemctl enable "$unit"
  done
if (( change )); then
  for unit in "${selected_units[@]}"; do
    if [[ $layout != glm || $unit == mag-llm-qwen-room.service && -n ${restart_unit[$unit]:-} ]]; then
      systemctl start "$unit"
    fi
  done
  ports=(8192)
  [[ $layout != glm ]] || ports=(8191)
  for port in "${ports[@]}"; do
    unit=$selected
    [[ $port != 8191 ]] || unit=mag-llm-qwen-room.service
    if [[ $layout == glm && -z ${restart_unit[$unit]:-} ]]; then continue; fi
    ready=0
    # A 98 GB cold load may take many minutes. The deploy watchdog is extended
    # for this layout; never roll back on a short generic health timeout.
    for ((i=0; i<2160; i++)); do
      systemctl is-active --quiet "$unit" || fail "$unit exited during load"
      if curl --noproxy '*' -fsS --max-time 3 -o /dev/null "http://127.0.0.1:$port/health" 2>/dev/null; then ready=1; break; fi
      sleep 5
    done
    (( ready )) || fail "model on $port health timeout after 10800s"
    if [[ $layout == glm ]]; then
      python3 -I "$ROOT/ops/qwen/smoke.py" --port 8191 --model qwen3.8-flash-next-uncensored --startup-only
      curl --noproxy '*' -fsS --max-time 30 -H 'Content-Type: application/json' -d '{}' "http://127.0.0.1:8191/slots/0?action=erase" >/dev/null
    elif [[ $layout == mixed ]]; then
      python3 -I "$ROOT/ops/qwen/smoke.py" --port "$port" --model qwen3.8-flash-next-uncensored
      curl --noproxy '*' -fsS --max-time 30 -H 'Content-Type: application/json' -d '{}' "http://127.0.0.1:$port/slots/0?action=erase" >/dev/null
    fi
  done
fi
if [[ $layout == glm ]]; then
  [[ $(systemctl show -p MainPID --value mag-llm-glm@q2.service 2>/dev/null || true) == "$q2_pid" ]] || fail 'GLM-Q2 PID changed during Qwen cold start'
  curl --noproxy '*' -fsS --max-time 5 -o /dev/null http://127.0.0.1:8192/health || fail 'GLM-Q2 :8192 unhealthy after Qwen start'
  systemctl is-active --quiet mag-llm-qwen-room.service || fail 'Qwen room service not active'
  curl --noproxy '*' -fsS --max-time 5 -o /dev/null http://127.0.0.1:8191/health || fail 'Qwen :8191 unhealthy after cold start'
fi
rm -f "$FAILED"
echo "Room layout ready: $layout ($source); Qwen NUMA0 + GLM-Q2 NUMA1; GLM-Q1 disabled"
