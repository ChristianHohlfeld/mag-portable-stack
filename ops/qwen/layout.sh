#!/usr/bin/env bash
# Transactional GLM layout switch: avoid evicting warm NUMA model units.
set -euo pipefail
ROOT=$(cd -- "$(dirname -- "$(readlink -f "$0")")/../.." && pwd)
# shellcheck source=../mag-units.sh
source "$ROOT/ops/mag-units.sh"
ACTION=${1:?apply|rollback|probe}; BACKUP=${2:?deployment backup directory}
TXN="$BACKUP/qwen-layout"
fail(){ echo "room layout: $*" >&2; exit 4; }
[[ $EUID == 0 ]] || fail 'root required'
units=(mag-llm-ds-node@1.service mag-llm-qwen.service mag-llm-qwen-room.service mag-llm-mistral-room.service mag-llm-glm.service mag-llm-glm@q1.service mag-llm-glm@q2.service)
[[ ! -f "$TXN/glm-dual" ]] || units+=(mag-llm-ds-node@0.service mag-llm-ornith-node@0.service mag-llm-ornith@1.service)
files=(/etc/mag-stack/room-model.env /etc/mag-stack/room-layout /etc/mag-stack/room-layout.pinned
       /etc/mag-qwen/runtime.env
       /etc/systemd/system/mag-llm-qwen.service /etc/systemd/system/mag-llm-qwen-room.service /etc/systemd/system/mag-llm-mistral-room.service /etc/systemd/system/mag-llm-ds-node@.service
       /usr/local/lib/mag-qwen/start.sh /usr/local/lib/mag-qwen/verify-model.sh
       /usr/local/lib/mag-qwen/runtime.sha /etc/mag-qwen/model.sha256
       /usr/local/lib/mag-mistral/start.sh /usr/local/lib/mag-mistral/verify-model.sh /usr/local/lib/mag-mistral/runtime.sha
       /etc/mag-mistral/SHA256SUMS /etc/mag-mistral/runtime.env /etc/mag-mistral/model.verified
       /etc/systemd/system/mag-audit-gateway.service.d/50-two-bots.conf
       /etc/mag-glm/q1.env /etc/mag-glm/q2.env
       /usr/local/lib/mag-glm/start.sh /usr/local/lib/mag-glm/runtime.sha
       /usr/local/lib/mag-glm/model.sizes /usr/local/lib/mag-glm/model.sha256
       /usr/local/lib/mag-glm/verify-model.sh
       /usr/local/lib/mag-glm/ik/0001-glm5next-legacy-6block-hparams.patch
       /usr/local/lib/mag-glm/verify-iq1.sh
       /etc/systemd/system/mag-llm-glm@.service
       /etc/systemd/system/mag-llm-glm@q1.service.d/10-numa.conf
       /etc/systemd/system/mag-llm-glm@q2.service.d/10-numa.conf)
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
q2_wait_port_free(){
  # ik_llama.cpp's cpp-httplib binds with SO_REUSEPORT only (no SO_REUSEADDR),
  # so it cannot bind :8192 while the stopped mainline Q2 still has TIME_WAIT
  # sockets there ("couldn't bind to server socket", seen live on #252).
  # Wait until 127.0.0.1:8192 has no sockets left (TIME_WAIT lasts 60 s).
  local i
  for ((i=0; i<90; i++)); do
    [[ -z $(ss -Htan '( sport = :8192 )' 2>/dev/null) ]] && return 0
    sleep 2
  done
  return 1
}
q2_engine_from_file(){
  local value
  value=$(sed -n 's/^GLM_ENGINE=//p' "$1" 2>/dev/null | tail -n1)
  printf '%s' "${value:-mainline}"
}
validate_q2_engine(){
  local engine=$1 rev patch dir bin rev_override=${2:-}
  case $engine in
    mainline)
      rev=${rev_override:-$(cat /usr/local/lib/mag-glm/runtime.sha 2>/dev/null || true)}
      [[ $rev =~ ^[a-f0-9]{40}$ ]] || fail 'Q2 mainline runtime sha invalid'
      dir="/home/chris/.local/qwen4exp/$rev-mag-cp1"
      bin="$dir/bin/llama-server"
      [[ -x $bin && $(cat "$dir/bin/source-commit" 2>/dev/null || true) == "$rev" ]] || fail 'Q2 mainline rollback runtime missing or unpinned'
      ;;
    ik)
      patch=$(sha256sum "$ROOT/ops/glm/ik/0001-glm5next-legacy-6block-hparams.patch" | awk '{print $1}' | sha256sum | cut -c1-12)
      dir="/home/chris/.local/glm-ik/8d21687cc7-mag-$patch"
      bin="$dir/bin/llama-server"
      [[ -x $bin && $(cat "$dir/source-commit" 2>/dev/null || true) == 8d21687cc7358de10264d4180dba9b863024d5c4 ]] || fail 'Q2 ik runtime missing or source commit mismatch'
      [[ $(cat "$dir/patch-identity" 2>/dev/null || true) == "$patch" ]] || fail 'Q2 ik patch identity mismatch'
      [[ -f "$dir/SHA256SUMS" ]] || fail 'Q2 ik binary checksum missing'
      (cd "$dir" && sha256sum --check --strict --status SHA256SUMS) || fail 'Q2 ik binary checksum mismatch'
      ;;
    *) fail "unsupported Q2 engine: $engine" ;;
  esac
}
rollback(){
  [[ -f $TXN/started && ! -f $TXN/restored ]] || return 0
  # If the one-time Q2 engine switch failed, restore its old engine transactionally.
  if [[ -f $TXN/q2-engine-switch-started ]] && systemctl is-active --quiet mag-llm-glm@q2.service; then
    systemctl stop mag-llm-glm@q2.service || {
      echo 'FAIL: cannot stop the new Q2 engine; leaving its matching config in place' >&2
      return 1
    }
  fi
  # Roll back the NUMA0 room-model cutover; Q2 is handled only by its own switch marker.
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
  if [[ -f $TXN/q2-engine-switch-started ]] && grep -Fxq mag-llm-glm@q2.service "$TXN/active"; then
    q2_wait_port_free || echo 'WARNING: :8192 still has sockets before Q2 rollback start' >&2
    systemctl start mag-llm-glm@q2.service || echo 'WARNING: Q2 rollback start failed' >&2
    q2_rollback_ready=0
    for ((ri=0; ri<5040; ri++)); do
      if systemctl is-active --quiet mag-llm-glm@q2.service &&
         curl --noproxy '*' -fsS --max-time 3 -o /dev/null http://127.0.0.1:8192/health 2>/dev/null; then
        q2_rollback_ready=1; break
      fi
      sleep 5
    done
    (( q2_rollback_ready )) || echo 'WARNING: previous Q2 engine restored but :8192 health is unavailable' >&2
  fi
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
  # Rollback smoke checks are observational only; never undo a completed restore.
  if [[ -f $TXN/changed ]] && grep -Fxq mag-llm-qwen-room.service "$TXN/active"; then
    qwen_ready=0
    for ((qi=0; qi<120; qi++)); do
      if systemctl is-active --quiet mag-llm-qwen-room.service &&
         curl --noproxy '*' -fsS --max-time 3 -o /dev/null http://127.0.0.1:8191/health 2>/dev/null; then
        qwen_ready=1; break
      fi
      sleep 5
    done
    if (( qwen_ready )); then
      python3 -I "$ROOT/ops/qwen/smoke.py" --port 8191 --model qwen3.8-flash-next-uncensored --quick ||
        echo 'WARNING: Qwen rollback chat smoke failed' >&2
    else
      echo 'WARNING: Qwen rollback service restored, health still unavailable' >&2
    fi
  fi
  if [[ -f $TXN/changed ]] && grep -Fxq mag-llm-mistral-room.service "$TXN/active"; then
    mistral_ready=0
    for ((mi=0; mi<120; mi++)); do
      if systemctl is-active --quiet mag-llm-mistral-room.service &&
         curl --noproxy '*' -fsS --max-time 3 -o /dev/null http://127.0.0.1:8191/health 2>/dev/null; then
        mistral_ready=1; break
      fi
      sleep 5
    done
    if (( mistral_ready )); then
      python3 -I "$ROOT/ops/mistral/smoke.py" --port 8191 --timeout 600 ||
        echo 'WARNING: Mistral rollback chat smoke failed' >&2
    else
      echo 'WARNING: Mistral rollback service restored, health still unavailable' >&2
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
id_files=("$ROOT/ops/qwen/layout.sh" "$ROOT/ops/qwen/runtime.sha"
          "$ROOT/ops/room/$env_file" "$ROOT/ops/room/layout"
          "$ROOT/ops/qwen/verify-model.sh" "$ROOT/ops/qwen/smoke.py"
          "$ROOT/ops/glm/arch-compat.patch")
if [[ $layout == glm ]]; then
  id_files+=("$ROOT/ops/mistral/runtime.sha" "$ROOT/ops/mistral/SHA256SUMS"
             "$ROOT/ops/mistral/build-runtime.sh" "$ROOT/ops/mistral/install.sh"
             "$ROOT/ops/mistral/start.sh" "$ROOT/ops/mistral/verify-model.sh"
             "$ROOT/ops/mistral/fetch-hf-sha256.py" "$ROOT/ops/mistral/mag-llm-mistral-room.service" "$ROOT/ops/mistral/smoke.py"
             "$ROOT/ops/glm/start.sh" "$ROOT/ops/glm/q2.env" "$ROOT/ops/glm/install.sh"
             "$ROOT/ops/glm/build-ik-glm.sh" "$ROOT/ops/glm/ik/0001-glm5next-legacy-6block-hparams.patch"
             "$ROOT/ops/glm/mag-llm-glm@.service" "$ROOT/ops/glm/mag-llm-glm@q2.service.d/10-numa.conf"
             "$ROOT/ops/deploy.sh")
fi
identity="$layout $(cat "${id_files[@]}" 2>/dev/null | sha256sum | cut -c1-16)"
# The probe is read-only and runs BEFORE the deploy's backup and service staging.
# Never turn a failed desired layout into a successful deploy of the old one.
if [[ $source == repo && -f $FAILED && $(cat "$FAILED") == "$identity" ]]; then
  echo "room layout blocked: repo layout '$layout' previously failed live ($identity); previous '$previous' retained" >&2
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
if [[ $layout == mixed ]]; then
  [[ -s /home/chris/models/qwen3.8-flash-next-uncensored/upstream.sha256 ]] || fail 'Qwen download/upstream.sha256 missing'
fi
install -d -m 0700 "$TXN"
[[ ! -e $TXN/started ]] || fail 'transaction already exists'
if [[ $layout == glm ]]; then
  q2_pid=$(systemctl show -p MainPID --value mag-llm-glm@q2.service 2>/dev/null || true)
  [[ $q2_pid =~ ^[1-9][0-9]*$ ]] || fail 'GLM-Q2 must already be running; refusing an implicit start'
  systemctl is-enabled --quiet mag-llm-glm@q2.service || fail 'GLM-Q2 must already be enabled'
  curl --noproxy '*' -fsS --max-time 5 -o /dev/null http://127.0.0.1:8192/health || fail 'GLM-Q2 :8192 is not healthy'
  q2_engine_before=$(q2_engine_from_file /etc/mag-glm/q2.env)
  q2_mainline_before=$(cat /usr/local/lib/mag-glm/runtime.sha 2>/dev/null || true)
  q2_identity_before=$(systemctl show -p ActiveEnterTimestamp -p MainPID mag-llm-glm@q2.service)
  q2_expected_pid=$q2_pid
  if runs_stale_binary mag-llm-glm@q2.service; then
    echo 'WARN: GLM-Q2 runs a deleted executable; it is restarted only if its selected engine changes' >&2
  fi
  REV=$(cat "$ROOT/ops/mistral/runtime.sha")
  BIN="/home/chris/.local/mistral/$REV-mag-cpu/bin/llama-server"
  if [[ ! -x $BIN || $(cat "$(dirname "$BIN")/source-commit" 2>/dev/null || true) != "$REV" ]]; then
    runuser -u chris -- env HOME=/home/chris nice -n 19 ionice -c3 bash "$ROOT/ops/mistral/build-runtime.sh"
  fi
  [[ -x $BIN && $(cat "$(dirname "$BIN")/source-commit" 2>/dev/null || true) == "$REV" ]] || fail 'pinned Mistral4 runtime build missing'
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
    if [[ -z $pin && $source == repo ]] && { [[ $layout != glm ]] || [[ -f $TXN/model-verified ]]; }; then
      install -d -m 0755 "$(dirname "$FAILED")" && printf '%s\n' "$identity" > "$FAILED" || true
    fi
    rollback
  fi
  exit "$rc"
}
trap on_exit EXIT
# Verify the chosen NUMA0 model completely before stopping the current Qwen service.
if [[ $layout == mixed ]]; then
  bash "$ROOT/ops/qwen/install.sh" refresh
elif [[ $layout == glm ]]; then
  bash "$ROOT/ops/glm/install.sh" refresh
  bash "$ROOT/ops/mistral/install.sh" stage
  # Both model definitions are staged; Mistral's weights are SHA256-verified.
  # Build/preflight failures remain retryable; only a live switch attempt is remembered.
  # The Q2-only physical-core drop-in must be active before its one controlled restart.
  mag_flush_daemon_reload
  q2_engine_after=$(q2_engine_from_file /etc/mag-glm/q2.env)
  if [[ $q2_engine_after == ik ]]; then
    ik_patch_id=$(sha256sum "$ROOT/ops/glm/ik/0001-glm5next-legacy-6block-hparams.patch" | awk '{print $1}' | sha256sum | cut -c1-12)
    ik_runtime="/home/chris/.local/glm-ik/8d21687cc7-mag-$ik_patch_id/bin/llama-server"
    if [[ ! -x $ik_runtime ]]; then
      runuser -u chris -- env HOME=/home/chris nice -n 19 ionice -c3 bash "$ROOT/ops/glm/build-ik-glm.sh"
    fi
  fi
  validate_q2_engine "$q2_engine_after"
  validate_q2_engine "$q2_engine_before" "$q2_mainline_before"
  touch "$TXN/model-verified"
  if [[ $q2_engine_before != "$q2_engine_after" ]]; then
    printf '%s\n' "$q2_engine_before" > "$TXN/q2-engine-before"
    printf '%s\n' "$q2_engine_after" > "$TXN/q2-engine-after"
    printf '1\n' > "$TXN/q2-engine-restart-count"
    touch "$TXN/q2-engine-switch-started"
    # Exactly one controlled Q2 stop/start. Q1/Mistral and NUMA0 are not involved.
    systemctl stop mag-llm-glm@q2.service
    q2_wait_port_free || fail 'GLM-Q2 :8192 still has sockets 180 s after stop'
    systemctl start mag-llm-glm@q2.service
    q2_ready=0
    for ((qi=0; qi<5040; qi++)); do
      systemctl is-active --quiet mag-llm-glm@q2.service || fail 'GLM-Q2 exited during engine switch'
      if curl --noproxy '*' -fsS --max-time 3 -o /dev/null http://127.0.0.1:8192/health 2>/dev/null; then
        q2_ready=1; break
      fi
      sleep 5
    done
    (( q2_ready )) || fail 'GLM-Q2 :8192 health timeout after engine switch'
    python3 -I "$ROOT/ops/qwen/smoke.py" --port 8192 --model glm-5.3-flash --quick
    q2_expected_pid=$(systemctl show -p MainPID --value mag-llm-glm@q2.service 2>/dev/null || true)
    q2_identity_after=$(systemctl show -p ActiveEnterTimestamp -p MainPID mag-llm-glm@q2.service)
    [[ $q2_expected_pid =~ ^[1-9][0-9]*$ && $q2_expected_pid != "$q2_pid" &&
       $q2_identity_after != "$q2_identity_before" ]] || fail 'Q2 engine switch did not produce exactly one new service identity'
    systemctl show -p ActiveEnterTimestamp -p MainPID mag-llm-glm@q2.service > "$TXN/q2-engine-identity"
    touch "$TXN/q2-engine-switched"
  fi
fi
install -m 0644 "$ROOT/ops/room/mag-llm-ds-node@.service" /etc/systemd/system/mag-llm-ds-node@.service
install -d -m 0755 /etc/mag-stack /etc/mag-qwen /etc/mag-mistral
case $layout in
  mixed) selected=mag-llm-qwen.service; config=mixed.env
         printf 'QWEN_NODE=1\nQWEN_PORT=8192\nQWEN_THREADS=14\nQWEN_THREADS_BATCH=14\nQWEN_MIN_RAM_GIB=150\n' > /etc/mag-qwen/runtime.env ;;
  glm)   selected=mag-llm-mistral-room.service; config=glm.env
         printf 'MISTRAL_THREADS=14\nMISTRAL_MIN_RAM_GIB=225\n' > /etc/mag-mistral/runtime.env ;;
  *)     selected=mag-llm-ds-node@1.service; config=model.env ;;
esac
selected_units=("$selected")
if [[ $layout == glm ]]; then selected_units=(mag-llm-mistral-room.service mag-llm-glm@q2.service); fi
others=()
for unit in "${units[@]}"; do
  keep=0
  for chosen in "${selected_units[@]}"; do [[ $unit != "$chosen" ]] || keep=1; done
  (( keep )) || others+=("$unit")
done
# The selected NUMA0 room model may start/restart; Q2 may restart only in the explicit engine transaction above.
change=0
declare -A restart_unit=()
for unit in "${selected_units[@]}"; do
  if ! systemctl is-active --quiet "$unit"; then
    restart_unit["$unit"]=1
    change=1
  fi
done
if [[ $layout == glm ]]; then
  mistral_changed=0
  for file in /usr/local/lib/mag-mistral/start.sh \
      /usr/local/lib/mag-mistral/verify-model.sh \
      /usr/local/lib/mag-mistral/runtime.sha \
      /etc/systemd/system/mag-llm-mistral-room.service \
      /etc/mag-mistral/SHA256SUMS /etc/mag-mistral/runtime.env; do
    cmp -s "$file" "$TXN/files$file" || mistral_changed=1
  done
  if (( mistral_changed )) || runs_stale_binary mag-llm-mistral-room.service; then
    restart_unit[mag-llm-mistral-room.service]=1
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
    if [[ $layout != glm || $unit == mag-llm-mistral-room.service && -n ${restart_unit[$unit]:-} ]]; then
      systemctl start "$unit"
    fi
  done
  ports=(8192)
  [[ $layout != glm ]] || ports=(8191)
  for port in "${ports[@]}"; do
    unit=$selected
    [[ $port != 8191 ]] || unit=mag-llm-mistral-room.service
    if [[ $layout == glm && -z ${restart_unit[$unit]:-} ]]; then continue; fi
    ready=0
    # A 233 GB cold mmap load can take hours on CPU. The deploy watchdog is
    # extended to the full service start budget; never use a short timeout.
    for ((i=0; i<5040; i++)); do
      systemctl is-active --quiet "$unit" || fail "$unit exited during load"
      if curl --noproxy '*' -fsS --max-time 3 -o /dev/null "http://127.0.0.1:$port/health" 2>/dev/null; then ready=1; break; fi
      sleep 5
    done
    (( ready )) || fail "model on $port health timeout after 25200s"
    if [[ $layout == glm ]]; then
      python3 -I "$ROOT/ops/mistral/smoke.py" --port 8191 --health-only
      curl --noproxy '*' -fsS --max-time 30 -H 'Content-Type: application/json' -d '{}' "http://127.0.0.1:8191/slots/0?action=erase" >/dev/null
    elif [[ $layout == mixed ]]; then
      python3 -I "$ROOT/ops/qwen/smoke.py" --port "$port" --model qwen3.8-flash-next-uncensored
      curl --noproxy '*' -fsS --max-time 30 -H 'Content-Type: application/json' -d '{}' "http://127.0.0.1:$port/slots/0?action=erase" >/dev/null
    fi
  done
fi
if [[ $layout == glm ]]; then
  mistral_ready=0
  for ((i=0; i<5040; i++)); do
    systemctl is-active --quiet mag-llm-mistral-room.service || fail 'Mistral room service exited'
    if curl --noproxy '*' -fsS --max-time 3 -o /dev/null http://127.0.0.1:8191/health 2>/dev/null; then
      mistral_ready=1; break
    fi
    sleep 5
  done
  (( mistral_ready )) || fail 'Mistral :8191 health timeout after 25200s'
  python3 -I "$ROOT/ops/mistral/smoke.py" --port 8191 --timeout 7200
  systemctl is-active --quiet mag-llm-glm@q2.service || fail 'GLM-Q2 became inactive during Mistral cold start'
  current_q2_pid=$(systemctl show -p MainPID --value mag-llm-glm@q2.service 2>/dev/null || true)
  [[ $current_q2_pid == "$q2_expected_pid" ]] || fail 'GLM-Q2 PID changed during Mistral cold start'
  curl --noproxy '*' -fsS --max-time 5 -o /dev/null http://127.0.0.1:8192/health || fail 'GLM-Q2 :8192 unhealthy after Mistral start'
fi
rm -f "$FAILED"
echo "Room layout ready: $layout ($source); Mistral NUMA0 + GLM-Q2 NUMA1; GLM-Q1 disabled"
