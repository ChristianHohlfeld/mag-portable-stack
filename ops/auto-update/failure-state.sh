#!/usr/bin/env bash
# Durable, SHA-scoped retry state for MAG's GitHub-green deployer.
# Called under /run/lock/mag-auto-update.lock; only changes $STATE, never models.
# First failure: 2 min; then 5, 15 and at most 60 minutes for repeat failures.
# New green SHA always bypasses a previous SHA's cooldown.
mag_retry_count() {
  local count=1
  if [[ -f "$STATE/failed-count" ]]; then
    count=$(cat "$STATE/failed-count" 2>/dev/null || echo 1)
  fi
  [[ "$count" =~ ^[0-9]+$ ]] || count=1
  (( count >= 1 )) || count=1
  (( count <= 100 )) || count=100
  printf '%s\n' "$count"
}
mag_retry_seconds() {
  local count=$1
  if (( count <= 1 )); then echo 120
  elif (( count == 2 )); then echo 300
  elif (( count == 3 )); then echo 900
  else echo 3600
  fi
}
mag_retry_remaining() {
  local candidate=$1 failed at now count delay age
  failed=$(cat "$STATE/failed-sha" 2>/dev/null || true)
  [[ "$candidate" == "$failed" ]] || { echo 0; return; }
  at=$(stat -c %Y "$STATE/failed-sha" 2>/dev/null || echo 0)
  [[ "$at" =~ ^[0-9]+$ ]] || at=0
  now=$(date +%s)
  count=$(mag_retry_count)
  delay=$(mag_retry_seconds "$count")
  age=$((now-at))
  (( age >= 0 )) || age=0
  if (( age < delay )); then echo $((delay-age)); else echo 0; fi
}
mag_record_failure() {
  local candidate=$1 stage=$2 count=1 previous
  previous=$(cat "$STATE/failed-sha" 2>/dev/null || true)
  if [[ "$candidate" == "$previous" ]]; then
    count=$(mag_retry_count)
    count=$(( count < 100 ? count+1 : 100 ))
  fi
  install -d -m 0755 "$STATE"
  printf '%s\n' "$count" > "$STATE/failed-count"
  printf '%s\n' "$stage" > "$STATE/failed-stage"
  # Write SHA last: its mtime is the retry clock.
  printf '%s\n' "$candidate" > "$STATE/failed-sha"
}
