#!/usr/bin/env bash
# One-shot removal of pre-4.0 leftovers on mag (approved by Chris 2026-10-17).
# Runs once from deploy.sh after a successful deploy; never fails the deploy.
# Anything still referenced by /etc, /usr/local, the current release or a
# running process is kept and reported. Models, certificates, secrets, Gitea
# data, room state, s23-native-udp and mag-llm/mag-llm-iq3 are never touched.
#   MAG_CLEANUP_DRY_RUN=1  only report
#   MAG_CLEANUP_FORCE=1    run again although the marker exists
set -uo pipefail
STAMP=${MAG_CLEANUP_STAMP:-/var/lib/mag-stack/cleanup-legacy.done}
REPORT=${MAG_CLEANUP_REPORT:-/var/lib/mag-stack/cleanup-report.txt}
HOME_DIR=${MAG_CLEANUP_HOME:-/home/chris}
KEEP_CHECKOUT=${MAG_CLEANUP_KEEP_CHECKOUT:-$HOME_DIR/mag-stack}
BUNDLES=${MAG_CLEANUP_BUNDLES:-/var/backups/mag-legacy-checkouts}
ROOM_STATE=${MAG_ROOM_STATE:-/var/lib/magbot/room}
FULL_BACKUPS=${MAG_BACKUP_ROOT:-/var/backups/mag-full}
UNIT_DIR=${MAG_CLEANUP_UNIT_DIR:-/etc/systemd/system}
TMP_DIR=${MAG_CLEANUP_TMP:-/tmp}
REF_DIRS=${MAG_CLEANUP_REF_DIRS:-/etc /usr/local/bin /usr/local/sbin /usr/local/lib $HOME_DIR/.local/bin $HOME_DIR/.config/systemd}
LEGACY_TOOLS=${MAG_CLEANUP_TOOLS:-/usr/local/sbin/mag-bootstrap-blank-ubuntu /usr/local/sbin/mag-apply-iac /usr/local/sbin/mag-collect-live-state /usr/local/lib/mag-common.sh}
LEGACY_BACKUPS=${MAG_CLEANUP_BACKUPS:-/var/backups/mag-restic /var/backups/mag-browser-pt-* /var/backups/mag-llm-* $HOME_DIR/mag-infra/systemd/*.bak-*}
ORPHAN_VMS=${MAG_CLEANUP_ORPHAN_VMS:-agent2}
MAGBOT=${MAG_CLEANUP_MAGBOT:-/usr/local/bin/magbot}
DRY=${MAG_CLEANUP_DRY_RUN:-0}

# The one-shot (checkouts, tools, units, orphan VMs) is stamped. Backup
# removal and the named loose-file pass run on every deploy: the first
# 4.0.1 pass kept the old backups because no verified full backup existed yet.
ONCE=1
[[ -e "$STAMP" && "${MAG_CLEANUP_FORCE:-0}" != 1 ]] && ONCE=0
mkdir -p "$(dirname "$REPORT")"
: > "$REPORT"
log() { printf '%s\n' "$*" | tee -a "$REPORT"; }
free_kib() { df -Pk / | awk 'NR==2{print $4}'; }
before=$(free_kib)
log "MAG legacy cleanup $(date -u +%FT%TZ)$([[ $DRY == 1 ]] && echo ' (dry run)')"

# Files that mention $1, excluding the candidates themselves.
referenced() {
  local path=$1 hit
  # shellcheck disable=SC2086
  hit=$(grep -rIlsF -- "$path" $REF_DIRS 2>/dev/null | grep -vxF -f <(printf '%s\n' $LEGACY_TOOLS) | head -3)
  [[ -n "$hit" ]] && { printf '%s' "$hit" | tr '\n' ' '; return 0; }
  # shellcheck disable=SC2086
  hit=$(find $REF_DIRS -maxdepth 2 -type l -lname "$path*" 2>/dev/null | head -1)
  [[ -n "$hit" ]] && { printf '%s' "$hit"; return 0; }
  return 1
}
in_use() {
  local path=$1 p
  for p in /proc/[0-9]*; do
    [[ "$(readlink "$p/exe" 2>/dev/null)" == "$path"* ]] && return 0
    [[ "$(readlink "$p/cwd" 2>/dev/null)" == "$path"* ]] && return 0
    grep -qsF -- " $path/" "$p/maps" 2>/dev/null && return 0
  done
  return 1
}
remove() {
  local path=$1 why=$2 size
  [[ -e "$path" || -L "$path" ]] || return 0
  size=$(du -sh -- "$path" 2>/dev/null | cut -f1)
  if [[ $DRY == 1 ]]; then log "would remove $path ($size): $why"; return 0; fi
  rm -rf -- "$path" && log "removed $path ($size): $why"
}

# 1. Legacy tools, once. Old backups are removed on every pass, and only
# after a verified full backup exists.
if (( ONCE )); then
declare -A tool_keep=()
for t in $LEGACY_TOOLS; do
  [[ -e "$t" ]] || continue
  if ref=$(referenced "$t"); then tool_keep[$t]="referenced by $ref"
  elif in_use "$t"; then tool_keep[$t]="in use by a running process"; fi
done
# A tool used by a kept tool stays as well (e.g. a sourced library).
changed=1
while (( changed )); do
  changed=0
  for t in $LEGACY_TOOLS; do
    [[ -e "$t" && -z "${tool_keep[$t]:-}" ]] || continue
    for k in "${!tool_keep[@]}"; do
      if grep -qsF -- "$t" "$k"; then tool_keep[$t]="needed by kept $k"; changed=1; break; fi
    done
  done
done
for t in $LEGACY_TOOLS; do
  if [[ -n "${tool_keep[$t]:-}" ]]; then log "kept $t: ${tool_keep[$t]}"; else remove "$t" "legacy tool"; fi
done
fi
verified_full=$(find "$FULL_BACKUPS" -mindepth 2 -maxdepth 2 -name VERIFIED 2>/dev/null | head -1)
for b in $LEGACY_BACKUPS; do
  [[ -e "$b" ]] || continue
  if [[ -z "$verified_full" ]]; then log "kept $b: no verified full backup in $FULL_BACKUPS yet"; continue; fi
  # Old backup data: config may still name it, so only a live user keeps it.
  if in_use "$b"; then log "kept $b: in use"; continue; fi
  remove "$b" "superseded by $(dirname "$verified_full")"
done

# Named leftovers in the home directory. Not a sweep: only these names,
# and only after a reference and in-use check. s23, models, src, mag-stack
# and the other kept trees are not in the list.
loose_home() {
  local pat f rel name tar list=()
  shopt -s nullglob
  for pat in stage-a\*.sh stage-a\*.out mag-deploy-\*.sh mag-deploy-\*.log gw-swap\* reload-q4.\* \
    fix-gitea.sh install-gitea-mag.sh setup-mag-ap.sh \
    numa-\*.log iq3-pre\*.log llama\*.log pages-activate\*.log s23-verify\*.log deploy-chain-a\*.log \
    crontab.bak-\* server-audit-20261014 grok-agent-test cxtrust-test; do
    for f in "$HOME_DIR"/$pat; do
      [[ -e "$f" || -L "$f" ]] || continue
      if ref=$(referenced "$f"); then log "kept $f: referenced by $ref"; continue; fi
      if in_use "$f"; then log "kept $f: in use"; continue; fi
      list+=("$f")
    done
  done
  shopt -u nullglob
  (( ${#list[@]} )) || return 0
  name=loose-home-$(date -u +%Y%m%dT%H%M%SZ)
  if [[ $DRY == 1 ]]; then
    for f in "${list[@]}"; do log "would remove $f: loose leftover (archive $BUNDLES/$name.tar.gz)"; done
    return 0
  fi
  install -d -m 0700 "$BUNDLES"
  tar=$(mktemp)
  : > "$tar"
  for f in "${list[@]}"; do printf '%s\n' "${f#"$HOME_DIR"/}" >> "$tar"; done
  if ! tar -C "$HOME_DIR" -czf "$BUNDLES/$name.tar.gz" -T "$tar"; then
    rm -f -- "$tar"
    log "kept loose leftovers: archive failed"
    return 0
  fi
  rm -f -- "$tar"
  for f in "${list[@]}"; do remove "$f" "loose leftover (archive: $BUNDLES/$name.tar.gz)"; done
}
loose_home

if (( ONCE )); then
# 2. Git checkouts in the home directory except the stack itself. Each one is
# bundled first (all refs, plus a list of uncommitted files).
while IFS= read -r git; do
  dir=$(dirname "$git")
  [[ -d "$dir" ]] || continue
  case "$dir" in "$KEEP_CHECKOUT"|"$KEEP_CHECKOUT"/*) continue ;; esac
  if find "$dir" -name '*.gguf' -print -quit 2>/dev/null | grep -q .; then log "kept $dir: contains model files"; continue; fi
  if ref=$(referenced "$dir"); then log "kept $dir: referenced by $ref"; continue; fi
  if in_use "$dir"; then log "kept $dir: in use by a running process"; continue; fi
  name=$(printf '%s' "${dir#"$HOME_DIR"/}" | tr '/' '_')
  g=(git -c safe.directory="$dir" -C "$dir")
  if [[ $DRY != 1 ]] && "${g[@]}" rev-parse -q --verify HEAD >/dev/null 2>&1; then
    install -d -m 0700 "$BUNDLES"
    if ! "${g[@]}" bundle create "$BUNDLES/$name.bundle" --all >/dev/null 2>&1; then
      log "kept $dir: git bundle failed"; continue
    fi
    "${g[@]}" status --porcelain > "$BUNDLES/$name.status" 2>/dev/null || :
  fi
  remove "$dir" "old checkout (bundle: $BUNDLES/$name.bundle)"
done < <(find "$HOME_DIR" -maxdepth 4 \( -path "$HOME_DIR/.cache" -o -path "$HOME_DIR/.codex" -o -path "$HOME_DIR/.local" \
           -o -path "$HOME_DIR/models" -o -path "$KEEP_CHECKOUT" -o -ipath '*gitea*' \) -prune -o -name .git -print 2>/dev/null | LC_ALL=C sort)

# 3. Test leftovers in /tmp older than a day.
while IFS= read -r t; do
  in_use "$t" && { log "kept $t: in use"; continue; }
  remove "$t" "test leftover"
done < <(find "$TMP_DIR" -mindepth 1 -maxdepth 1 ! -type s -mmin +1440 \
           \( -name 'mag-*' -o -name 'magnet-*' -o -name 'magbot-test*' -o -name 'mag_*' \) 2>/dev/null)

# 4. Orphan bot VMs: only the approved names, only if no room bot uses them.
for vm in $ORPHAN_VMS; do
  if grep -qsE "\"(vm|pending_vm)\"[[:space:]]*:[[:space:]]*\"$vm\"" "$ROOM_STATE/agents.json"; then
    log "kept VM $vm: used by a room bot"; continue
  fi
  if ! command -v virsh >/dev/null || ! virsh dominfo "magbot-$vm" >/dev/null 2>&1; then continue; fi
  if [[ $DRY == 1 ]]; then log "would despawn orphan VM magbot-$vm"; continue; fi
  if "$MAGBOT" despawn "$vm" >>"$REPORT" 2>&1; then log "despawned orphan VM magbot-$vm"
  else log "despawn of magbot-$vm failed (see above)"; fi
done

# 5. mag-* units whose program no longer exists, and timers without a unit.
reload=0
for unit in "$UNIT_DIR"/mag-*.service "$UNIT_DIR"/mag-*.timer; do
  [[ -f "$unit" && ! -L "$unit" ]] || continue
  name=$(basename "$unit"); dangling=""
  # Active release-owned units are not legacy, even if an optional binary
  # (cpupower on a blank test host) has not been installed yet.
  case "$name" in mag-llm*|mag-120*|mag-cpu-tune.service) continue ;; esac
  if [[ "$name" == *.service ]]; then
    while IFS= read -r exe; do
      exe=${exe#ExecStart=}
      while [[ "$exe" == [-+!@:]* ]]; do exe=${exe:1}; done
      exe=${exe%% *}
      [[ "$exe" == /* && "$exe" != *'$'* && ! -e "$exe" ]] && dangling="missing $exe"
    done < <(grep -E '^ExecStart=' "$unit")
  else
    target=$(sed -n 's/^Unit=//p' "$unit" | head -1); target=${target:-${name%.timer}.service}
    systemctl cat "$target" >/dev/null 2>&1 || dangling="missing $target"
  fi
  [[ -n "$dangling" ]] || continue
  if [[ $DRY == 1 ]]; then log "would remove unit $name: $dangling"; continue; fi
  systemctl disable --now "$name" >/dev/null 2>&1 || :
  rm -rf -- "$unit" "$unit.d"; reload=1
  systemctl reset-failed "$name" >/dev/null 2>&1 || :
  log "removed unit $name: $dangling"
done
if (( reload )); then
  # shellcheck source=mag-units.sh
  source "$(cd -- "$(dirname -- "$(readlink -f "$0")")" && pwd)/mag-units.sh"
  mag_daemon_reload || :
fi
if [[ $DRY != 1 ]]; then date -u +%FT%TZ > "$STAMP"; fi
else
log "one-shot sections already done ($STAMP); backup and loose-file passes still ran"
fi

after=$(free_kib)
log "freed $(( (after - before) / 1024 )) MiB; free on /: $(( after / 1024 / 1024 )) GiB"
failed=$(systemctl list-units --failed --plain --no-legend 2>/dev/null | awk '{print $1}' | tr '\n' ' ')
log "systemctl --failed: ${failed:-none}"
exit 0
