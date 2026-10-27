#!/usr/bin/env bash
# mag-test-timeout: 180
if [ -z "${MAG_TEST_DEADLINE_ARMED:-}" ]; then
  export MAG_TEST_DEADLINE_ARMED=1
  exec timeout --kill-after=5s 180s bash "$0" "$@"
fi
# Compatibility entry point for the old green promotion test name.
# GitHub alone is the release authority; Gitea statuses are not consulted.
set -euo pipefail
root=$(cd -- "$(dirname "$0")/../.." && pwd)
exec bash "$root/ops/tests/test-github-pipeline.sh" "$@"
