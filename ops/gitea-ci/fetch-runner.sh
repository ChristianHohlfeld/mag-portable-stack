#!/usr/bin/env bash
set -euo pipefail
[[ $EUID == 0 ]] || exit 1
ver=5.1.1
sha=07b7304cafac138152a9d34790c100af9493278ef63f097de47fa16071d768a1
if /usr/local/bin/gitea-runner --version 2>/dev/null | grep -Fq "$ver"; then exit 0; fi
tmp=$(mktemp -d);trap 'rm -rf "$tmp"' EXIT
curl --proto '=https' --tlsv1.2 -fsSL --retry 3 --max-time 300 \
  "https://gitea.com/gitea/runner/releases/download/v$ver/gitea-runner-$ver-linux-amd64.xz" -o "$tmp/runner.xz"
printf '%s  %s\n' "$sha" "$tmp/runner.xz" | sha256sum -c --quiet -
xz -dc "$tmp/runner.xz" > "$tmp/runner"
install -m 0755 -o root -g root "$tmp/runner" /usr/local/bin/.gitea-runner-next
mv -f /usr/local/bin/.gitea-runner-next /usr/local/bin/gitea-runner
