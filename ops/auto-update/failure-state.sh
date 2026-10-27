#!/usr/bin/env bash
# Failure diagnostics for the GitHub-green deployer.
# No cooldown or per-SHA retry lockout: a failed release is retried on
# the next normal timer tick. The single-flight flock in auto-update.sh
# still prevents concurrent deployments. New green commits are unaffected.
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
mag_retry_remaining() {
  # Never block a retry based on a previous failure.
  echo 0
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
  printf '%s\n' "$candidate" > "$STATE/failed-sha"
}
