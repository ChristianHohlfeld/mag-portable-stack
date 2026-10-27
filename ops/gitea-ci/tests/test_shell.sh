#!/usr/bin/env bash
# Reversible config/units/network/credential contract checks, no host services.
set -euo pipefail
root=$(cd -- "$(dirname "$0")/../../.." && pwd)
cd "$root"
bash -n ops/gitea-ci/*.sh ops/gitea-ci/suites/*.sh ops/gitea/bot-rights.sh
for unit in ops/gitea-ci/mag-ci-{vm,image}.service;do
 for line in 'CPUAffinity=54 55' 'CPUQuota=100%' 'MemoryMax=14G' 'MemorySwapMax=0' 'Nice=19' 'IOSchedulingClass=idle' 'Slice=mag-ci-workload.slice';do grep -Fq "$line" "$unit";done
done
! grep -Eq 'virtiofs|virtfs|9p|hostfwd|/var/lib/magbot|/dev/sd[a-z]' ops/gitea-ci/run-vm.sh
! grep -F 'LoadCredential=' ops/gitea-ci/mag-ci-vm.service ops/gitea-ci/mag-ci-guest-runner.service
! grep -E '(stop|restart|reload)[[:space:]]+(mag-llm|mag-120)' ops/gitea-ci/*.sh ops/gitea-ci/*.cpp
! grep -Eq 'sudoers|mag-deploy' ops/gitea-ci/guest-runner.yaml ops/gitea-ci/mag-ci-guest-runner.service
grep -Fq -- '--ephemeral' ops/gitea-ci/start-worker.sh
grep -Fq -- '--token-file' ops/gitea-ci/start-worker.sh
grep -Fq 'iifname "mag-ci" drop' ops/gitea-ci/network.sh
grep -Fq '100.64.0.0/10' ops/gitea-ci/network.sh
grep -Fq 'two-factor' ops/gitea-ci/README.md
# Configuration edits preserve existing secrets, repository paths and SSH.
tmp=$(mktemp -d);trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/bin"
printf '#!/bin/sh\nexit 0\n' > "$tmp/bin/chown";chmod +x "$tmp/bin/chown"
cat > "$tmp/app.ini" <<'INI'
[server]
ROOT_URL = https://old.tailnet:8445/
SSH_PORT = 2222
[security]
SECRET_KEY = fixture-private-key-do-not-print
[database]
PATH = /var/lib/gitea/data/gitea.db
[repository]
ROOT = /srv/repos-kept
INI
# Same editor, isolated paths. No production switch in the test.
sed -e "s|file=/etc/gitea/app.ini|file=$tmp/app.ini|" \
 -e "s|mktemp /etc/gitea/.public.|mktemp $tmp/.public.|" ops/gitea-ci/configure-gitea.sh > "$tmp/configure.sh"
PATH="$tmp/bin:$PATH" bash "$tmp/configure.sh"
cp "$tmp/app.ini" "$tmp/first.ini"
PATH="$tmp/bin:$PATH" bash "$tmp/configure.sh"
cmp "$tmp/first.ini" "$tmp/app.ini"
for line in 'TWO_FACTOR_AUTH = enforced' 'DISABLE_REGISTRATION = true' 'ROOT = /srv/repos-kept' 'SSH_PORT = 2222' 'SECRET_KEY = fixture-private-key-do-not-print';do grep -Fq "$line" "$tmp/app.ini";done
[[ $(grep -c '^\[security\]' "$tmp/app.ini") == 1 ]]
echo 'Gitea CI shell/config/isolation contracts PASS'
