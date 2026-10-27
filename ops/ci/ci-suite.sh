#!/usr/bin/env bash
# Run one Gitea suite, or pass the context when this diff does not touch it.
# Exit 1 from affected.sh is "not affected" and must stay a successful step:
# a Gitea skipped conclusion is not a green required context. Any other
# non-zero status is a filter or suite failure.
set -euo pipefail
suite=${1:?}
here=$(cd -- "$(dirname -- "$0")" && pwd)
st=0
bash "$here/affected.sh" "$suite" || st=$?
if [[ $st -eq 0 ]]; then
  bash "$here/../gitea-ci/suite.sh" "$suite"
elif [[ $st -eq 1 ]]; then
  echo "$suite: no matching path change; context passes"
else
  exit "$st"
fi
