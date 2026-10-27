#!/usr/bin/env bash
# Batch systemd daemon-reload across a deploy phase.
# Children cannot export a dirty flag to their parent, so the stamp lives in
# MAG_UNITS_DIRTY_FILE (usually $BACKUP/units.dirty or $BACKUP/late.dirty).
#   MAG_DEFER_DAEMON_RELOAD=1  mag_daemon_reload only touches the stamp
#   mag_flush_daemon_reload    one real reload when the stamp exists
#   mag_flush_daemon_reload --force  always reload (rollback)
[[ -n ${MAG_UNITS_SH:-} ]] && return 0
MAG_UNITS_SH=1

mag_units_dirty_file() {
  if [[ -n ${MAG_UNITS_DIRTY_FILE:-} ]]; then
    printf '%s\n' "$MAG_UNITS_DIRTY_FILE"
    return 0
  fi
  if [[ -n ${MAG_NGINX_TXN:-} ]]; then
    printf '%s\n' "$MAG_NGINX_TXN/units.dirty"
    return 0
  fi
  printf '%s\n' ''
}

mag_mark_units_dirty() {
  local stamp
  stamp=$(mag_units_dirty_file)
  [[ -n $stamp ]] || return 0
  mkdir -p "$(dirname "$stamp")"
  : >"$stamp"
}

mag_daemon_reload_exec() {
  timeout --kill-after=10s "${MAG_SYSTEMD_QUERY_S:-20}s" systemctl daemon-reload
}

mag_daemon_reload() {
  if [[ ${MAG_DEFER_DAEMON_RELOAD:-0} == 1 ]]; then
    mag_mark_units_dirty
    return 0
  fi
  mag_daemon_reload_exec
}

mag_flush_daemon_reload() {
  local force=0 stamp
  [[ ${1:-} == --force ]] && force=1
  stamp=$(mag_units_dirty_file)
  if [[ $force -eq 0 && -n $stamp && ! -e $stamp ]]; then
    return 0
  fi
  local saved=${MAG_DEFER_DAEMON_RELOAD:-0}
  MAG_DEFER_DAEMON_RELOAD=0
  mag_daemon_reload_exec
  MAG_DEFER_DAEMON_RELOAD=$saved
  [[ -n $stamp ]] && rm -f "$stamp"
}
