#!/usr/bin/env bash
# Static guards for the action runners: pinned downloads, unprivileged capped
# units, deploy only through the auto-update path, models never touched.
set -euo pipefail
root=$(cd -- "$(dirname "$0")/../.." && pwd)
r="$root/ops/runners"
bash -n "$r/install.sh" "$r/bot-runners.sh" "$r/status.sh" "$root/ops/gitea/enable-actions.sh" \
  "$root/ops/tests/e2e-gitea-actions.sh" "$root/ops/auto-update/apply.sh" "$root/ops/auto-update/auto-update.sh"
grep -Eq '^GITEA_RUNNER_SHA=[0-9a-f]{64}$' "$r/install.sh"
grep -Eq '^GH_RUNNER_SHA=[0-9a-f]{64}$' "$r/install.sh"
grep -Fq 'sha256sum -c' "$r/install.sh"
for unit in "$r/mag-gitea-runner.service" "$r/mag-github-runner.service"; do
  for line in 'NoNewPrivileges=yes' 'ProtectSystem=strict' 'ProtectHome=yes' 'MemoryMax=' 'CPUQuota=' 'OOMScoreAdjust=1000'; do
    grep -Fq "$line" "$unit"
  done
  grep -Eq '^User=mag-(gh)?runner$' "$unit"
  grep -Eq '^InaccessiblePaths=.*/etc/credstore.*/var/lib/magbot.*/var/lib/mag-llm' "$unit"
done
grep -Fq 'Unit=mag-auto-update.service' "$r/mag-deploy-request.path"
grep -Fq 'PathModified=/var/lib/mag-deploy/request' "$r/mag-deploy-request.path"
! grep -Eq 'sudo|sudoers' "$r/install.sh" "$r"/*.service
! grep -Eq 'mag-llm(\.service|-iq3)|systemctl (stop|restart) mag-llm' "$r"/* "$root/ops/auto-update"/*
grep -Fq 'ops/runners/install.sh' "$root/ops/auto-update/apply.sh"
grep -Fq 'exec bash "$ROOT/ops/auto-update/apply.sh"' "$root/ops/auto-update/auto-update.sh"
grep -Fq "vars.MAG_SELF_HOSTED == 'on'" "$root/.github/workflows/green.yml"
grep -Fq "needs.promote-green.outputs.promoted == 'true'" "$root/.github/workflows/green.yml"
grep -Fq 'mag:host' "$r/gitea-runner.yaml"
# Bot-org runners: org scope only, never deploy rights, strict sandbox.
b="$r/mag-gitea-botrunner@.service"
for line in 'User=magrun-%i' 'Group=mag-botrunner' 'NoNewPrivileges=yes' 'CapabilityBoundingSet=' \
  'ProtectSystem=strict' 'ProtectHome=yes' 'PrivateTmp=yes' 'PrivateDevices=yes' 'RestrictSUIDSGID=yes' \
  'ProtectKernelTunables=yes' 'ProtectKernelModules=yes' 'ProtectKernelLogs=yes' 'ProtectControlGroups=yes' \
  'LockPersonality=yes' 'RestrictNamespaces=yes' 'SystemCallFilter=@system-service' 'TemporaryFileSystem=/var/lib:ro' \
  'Slice=mag-botrunner.slice' 'IOSchedulingClass=idle' 'OOMScoreAdjust=1000' 'Nice=15' \
  'ExecStartPre=+/usr/sbin/nft -f /etc/mag-botrunner/egress.nft' 'ConditionPathExists=/var/lib/mag-botrunner/%i/.runner'; do
  grep -Fxq "$line" "$b" || { echo "missing in $b: $line" >&2; exit 1; }
done
grep -Eq '^MemoryMax=[0-9]+G$' "$b"; grep -Eq '^CPUQuota=[0-9]+%$' "$b"
grep -Eq '^IPAddressDeny=.*10\.0\.0\.0/8.*100\.64\.0\.0/10.*192\.168\.0\.0/16' "$b"
grep -Eq '^InaccessiblePaths=.*/etc/credstore .*/etc/gitea .*/opt ' "$b"
! grep -Eq 'mag-deploy|SupplementaryGroups|ReadWritePaths=.*/var/lib/mag-deploy|AmbientCapabilities=.' "$b"
! grep -Eq 'usermod -aG mag-deploy|SupplementaryGroups' "$r/bot-runners.sh"
grep -Fq 'gpasswd -d "$user" mag-deploy' "$r/bot-runners.sh"
grep -Fq 'MemoryMax=' "$r/mag-botrunner.slice"
grep -Fq 'meta skgid @GID@ jump botrunner' "$r/botrunner-egress.nft"
grep -Fq 'ip daddr 127.0.0.1 tcp dport @PORT@ accept' "$r/botrunner-egress.nft"
grep -Fq 'mag-bot:host' "$r/botrunner.yaml"
# Every runner token is scoped (bot org or the opted-in owner), never global,
# and only ever passed on stdin, never on a command line.
! cat "$r/install.sh" "$r/bot-runners.sh" | grep -v '^ *#' | grep 'generate-runner-token' | grep -vq 'generate-runner-token --scope "'
grep -Fq 'generate-runner-token --scope "$org"' "$r/bot-runners.sh"
! grep -Eq 'register .*--token[ =]|--token "\$' "$r/install.sh" "$r/bot-runners.sh"
grep -Fq -- '--token-file /dev/stdin' "$r/bot-runners.sh"
# The host runner (deploy rights) only on explicit owner opt-in.
grep -Fq '/etc/mag-stack/host-runner.owner' "$r/install.sh"
grep -Fq 'bots-|magbot-|magrun-' "$r/install.sh"
grep -Fq 'ops/runners/install.sh' "$root/ops/tests/e2e-gitea-actions.sh"
grep -Fq '/usr/local/sbin/mag-gitea-bot-runners' "$root/mag-room/native/vm.cpp"
if command -v systemd-analyze >/dev/null; then
  for u in "$b" "$r/mag-botrunner.slice"; do
    out=$(systemd-analyze verify "$u" 2>&1 || true)
    ! grep -Ei 'unknown (key|lvalue)|failed to parse|invalid' <<<"$out" || { echo "$out" >&2; exit 1; }
  done
fi
echo 'Runner guards PASS'
