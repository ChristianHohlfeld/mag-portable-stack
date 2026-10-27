#!/usr/bin/env bash
# Fallback when gitleaks is not installed. Prints file names only.
set -euo pipefail
pat="-----BEGIN [A-Z ]*PRIVATE KEY-----|gh[pousr]_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{40,}|AKIA[0-9A-Z]{16}|xox[baprs]-[A-Za-z0-9-]{10,}|sk-[A-Za-z0-9]{40,}|tskey-[a-z]+-[A-Za-z0-9]{10,}"
allow="^(server-audit-|mag-room/tests/test_guest_description[.]cpp$)"
hits=$(git ls-files -z | grep -zvE "$allow" | xargs -0 grep -lIE -e "$pat" -- 2>/dev/null || true)
[[ -z $hits ]] || { echo "possible secrets in:"; echo "$hits"; exit 1; }
