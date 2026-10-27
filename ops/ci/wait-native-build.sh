#!/usr/bin/env bash
# Block until ops/ci/native-build.sh writes MAG_CI_BUILD_OK, or fail if it
# wrote MAG_CI_BUILD_FAIL. Used so binary tests can start in the same
# fail-fast pool as the build.
set -euo pipefail
ok=${MAG_CI_BUILD_OK:?}
fail=${MAG_CI_BUILD_FAIL:?}
i=0
while ((i < 900)); do
  [[ -f $ok ]] && exit 0
  if [[ -f $fail ]]; then
    echo 'wait-native-build: native build failed' >&2
    exit 1
  fi
  sleep 0.2
  i=$((i + 1))
done
echo 'wait-native-build: timed out' >&2
exit 1
