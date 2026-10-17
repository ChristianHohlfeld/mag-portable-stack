#!/usr/bin/env bash
# No Gitea service or network required: tests real VM helper against stubbed
# Git credential helper/API with another-owner records and owner mismatch.
set -euo pipefail
here="$(cd "$(dirname "$0")/../../.." && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/bin"
cat >"$tmp/bin/git" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
if [[ "$1" == config ]]; then printf '%s\n' "$PAGES_TEST_GIT_USER"; exit 0; fi
if [[ "$1" == credential ]]; then
 printf 'username=%s\npassword=FAKE_TEST_ONLY\n' "$PAGES_TEST_GIT_USER"; exit 0
fi
echo 'Unexpected git call' >&2; exit 88
EOF
cat >"$tmp/bin/curl" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
last=
for arg in "$@"; do last="$arg"; done
case "$last" in
  */api/v1/user) echo '{"login":"magbot-agent1"}' ;;
  *'/api/v1/user/repos?limit=100&page=1')
    printf '%s\n' '[{"name":"own-site","owner":{"login":"magbot-agent1"},"private":true},{"name":"foreign","owner":{"login":"magbot-other"},"private":false}]' ;;
  */api/v1/repos/magbot-agent1/own-site)
    echo '{"owner":{"login":"magbot-agent1"},"default_branch":"main"}' ;;
  *) echo 'Unexpected API path' >&2; exit 88 ;;
esac
EOF
chmod +x "$tmp/bin/"*
export PATH="$tmp/bin:$PATH" PAGES_TEST_GIT_USER=magbot-agent1
out="$(bash "$here/ops/pages/pages-vm.sh" list placeholder magbot-agent1)"
grep -Fq '"repo":"own-site"' <<<"$out"
! grep -Fq '"repo":"foreign"' <<<"$out"
! grep -Fq 'FAKE_TEST_ONLY' <<<"$out"
bash "$here/ops/pages/pages-vm.sh" verify own-site magbot-agent1 |
  grep -Fq 'verified'
# The native adapter must never delegate mutations to a different owner.
if bash "$here/ops/pages/pages-vm.sh" verify own-site magbot-other >"$tmp/fail" 2>&1; then
 echo 'Owner spoof accepted' >&2; exit 1
fi
if bash "$here/ops/pages/pages-vm.sh" verify '../other' magbot-agent1 >/dev/null 2>&1; then
 echo 'Path traversal accepted' >&2; exit 1
fi
echo 'Pages VM Gitea owner isolation, list filter and secret-free output: PASS'
