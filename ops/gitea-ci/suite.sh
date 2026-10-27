#!/usr/bin/env bash
# Runs from the trusted control checkout. Candidate code lives in /work/source.
set -euo pipefail
[[ ${1:-} =~ ^(native|pages|nginx|recovery|room|chromium|speech|e2e-bootstrap|gitleaks|secrets)$ ]] || exit 64
suite=$1
control=$(cd -- "$(dirname -- "$0")" && pwd)
cd /work/source
[[ $(git rev-parse HEAD) == "${MAG_CI_SHA:?}" ]] || exit 1
[[ $MAG_CI_SHA =~ ^[0-9a-f]{40}$ && ${MAG_CI_BASE:?} =~ ^[0-9a-f]{40}$ ]] || exit 1
# Credentials used for checkout are gone before any candidate code executes.
[[ ! -e /run/mag-ci-checkout ]] || exit 1
if [[ $suite == gitleaks || $suite == secrets ]]; then
  if [[ ! -f /work/gitleaks.passed ]]; then
    gitleaks detect --no-banner --redact --source . --log-opts="${MAG_CI_BASE}..${MAG_CI_SHA}"
    # Main must also be scanned when base=head (an empty range is not proof).
    [[ $MAG_CI_BASE != "$MAG_CI_SHA" ]] || gitleaks detect --no-banner --redact --source . --log-opts="-1 ${MAG_CI_SHA}"
    touch /work/gitleaks.passed
  fi
else
  bash "$control/suites/$suite.sh"
fi
