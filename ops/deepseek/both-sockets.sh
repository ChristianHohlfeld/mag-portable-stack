#!/usr/bin/env bash
# Retired 2026-10-20: destructive Q4 both-socket migration.
# Keep this pathname as a fail-closed compatibility entrypoint for older
# operator jobs. No inference process is stopped or restarted here.
set -euo pipefail
echo 'RETIRED: both-sockets is disabled. It stopped IQ3 and destroyed warm Q4 KV cache.' >&2
echo 'Use ops/perf/diagnose-ai.sh and ops/deepseek/restore-dual.sh instead.' >&2
exit 4
