#!/usr/bin/env bash
# Local replacement for the GitHub workflows (native.yml, room-e2e.yml,
# image-paste.yml and the green.yml prerequisites) while Actions is blocked.
# Run in a scratch checkout, as a normal user: it builds into ./build, uses
# temporary state dirs and random ports, and never starts/stops/reloads any
# system service (mag-120, mag-llm, ornith, nginx). Checks that need root,
# podman, a real nginx or the GitHub API print SKIP. Every check runs; the
# script exits non-zero if any check failed. Every child is killed on exit,
# timeout, and abort, including servers that called setsid.
#   bash ops/ci/local-ci.sh [--jobs N] [--no-browser]
set -uo pipefail
ROOT=$(cd -- "$(dirname -- "$(readlink -f "$0")")/../.." && pwd)
cd "$ROOT" || exit 2
# shellcheck disable=SC1091 # reap.sh is passed to shellcheck on its own in native-fast.list
source "$ROOT/ops/ci/reap.sh"
mag_ci_arm "$ROOT/ops/ci/ci_reap.py" "$@"
JOBS=4 BROWSER=1
while (($#)); do
  case $1 in
    --jobs) JOBS=${2:?}; shift 2 ;;
    --no-browser) BROWSER=0; shift ;;
    *) echo "usage: $0 [--jobs N] [--no-browser]" >&2; exit 64 ;;
  esac
done
# Test hook used by ops/ci/tests/test-ci-reap.sh. One command, then the exit
# trap reaps anything it left in its own session.
if [[ -n ${MAG_CI_REAP_PROBE:-} ]]; then
  bash -c "$MAG_CI_REAP_PROBE" || exit $?
  exit 0
fi
LOG=$(mktemp -d /tmp/mag-local-ci.XXXXXX)
declare -a RESULTS=()
FAIL=0
check() { # check <name> <command...>
  local name=$1; shift
  local out
  out="$LOG/$(tr -c 'a-zA-Z0-9' '_' <<<"$name").log"
  # Hard cap so a hung room, build or network check cannot run forever.
  # --foreground keeps #147's signal on this command; ci_reap kills its session.
  if timeout --foreground 1200 python3 "$ROOT/ops/ci/ci_reap.py" --supervise -- "$@" >"$out" 2>&1; then
    RESULTS+=("PASS  $name")
    # a python unittest that only skipped is reported as SKIP
    if grep -Eq '^OK \(skipped=([0-9]+)\)$' "$out" && ! grep -Eq '\.\.\. ok$' "$out"; then
      RESULTS[-1]="SKIP  $name (all tests skipped, see $out)"
    fi
  else
    RESULTS+=("FAIL  $name (log: $out)"); FAIL=1
    tail -n 20 "$out" | sed 's/^/    /'
  fi
  printf '%s\n' "${RESULTS[-1]}"
}
skip() { RESULTS+=("SKIP  $1: $2"); printf '%s\n' "${RESULTS[-1]}"; }
have() { command -v "$1" >/dev/null 2>&1; }

launch=()
if command -v ccache >/dev/null; then
  export CCACHE_DIR="${CCACHE_DIR:-$HOME/.cache/ccache}"
  mkdir -p "$CCACHE_DIR"
  launch+=(-DCMAKE_C_COMPILER_LAUNCHER=ccache -DCMAKE_CXX_COMPILER_LAUNCHER=ccache)
fi
check 'build (cmake/ninja Release)' bash -c "cmake -S . -B build -G Ninja -DCMAKE_BUILD_TYPE=Release ${launch[*]} && cmake --build build -j $JOBS"
check 'test and workflow deadlines' python3 ops/ci/check-timeouts.py

# native.yml — one pool on this machine. GitHub runs the same lists as
# separate jobs; the room suite is sharded inside that pool.
skip 'tailscale egress nft regression' 'needs root and modifies nft (github-hosted only)'
skip 'nginx -t of the private site' 'needs sudo nginx (github-hosted only)'
parallel() {
  local timeout=$1 file=$2
  local out
  out="$LOG/parallel-$(basename "$file").log"
  if bash ops/ci/run-parallel.sh --jobs "$JOBS" --timeout "$timeout" < "$file" >"$out" 2>&1; then
    RESULTS+=("PASS  parallel $(basename "$file")")
  else
    RESULTS+=("FAIL  parallel $(basename "$file") (log: $out)"); FAIL=1
    tail -n 40 "$out" | sed 's/^/    /'
  fi
  printf '%s\n' "${RESULTS[-1]}"
}
chmod +x build/test-* build/magroom build/mag-audit-gateway build/mag-numa-swap build/s23ctl
gate_out="$LOG/merge-gate.log"
if MAG_CI_JOBS=$JOBS bash ops/ci/native-gate.sh --parallel >"$gate_out" 2>&1; then
  RESULTS+=("PASS  native merge gate")
else
  RESULTS+=("FAIL  native merge gate (log: $gate_out)"); FAIL=1
  tail -n 40 "$gate_out" | sed 's/^/    /'
fi
printf '%s\n' "${RESULTS[-1]}"
grep -E '^(STEP|FAIL|SLOW|PARALLEL|MERGE GATE) ' "$gate_out" || true
extra=$(mktemp)
{
  echo 'vm archive :: python3 mag-room/tests/test_vm_archive.py'
  echo 'retire DeepSeek :: bash ops/tests/test-retire-deepseek.sh'
} > "$extra"
parallel 120 "$extra"
rm -f "$extra"
if have gitleaks; then
  check 'gitleaks (no secrets)' gitleaks detect --no-banner --redact --source .
else
  # Fallback: tracked files must not contain private keys or token-shaped strings.
  # Prints file names only, never the matched text.
  # shellcheck disable=SC2016 # inner bash -c: $names expand in that script, not here
  check 'no-secrets grep (gitleaks not installed)' bash -c '
    pat="-----BEGIN [A-Z ]*PRIVATE KEY-----|gh[pousr]_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{40,}|AKIA[0-9A-Z]{16}|xox[baprs]-[A-Za-z0-9-]{10,}|sk-[A-Za-z0-9]{40,}|tskey-[a-z]+-[A-Za-z0-9]{10,}"
    # Allowlist: redaction-test fixtures with dummy token shapes.
    allow="^(server-audit-|mag-room/tests/test_guest_description[.]cpp$)"
    hits=$(git ls-files -z | grep -zvE "$allow" | xargs -0 grep -lIE -e "$pat" -- 2>/dev/null)
    [[ -z $hits ]] || { echo "possible secrets in:"; echo "$hits"; exit 1; }'
fi
if have go; then
  check 'browser WebRTC gateway (go test/build)' bash -c "cd mag-room/webrtc && go test -mod=mod -timeout 60s ./... && go build -mod=mod -o ${LOG}/mag-browser-webrtc ."
else skip 'browser WebRTC gateway' 'go not installed'; fi
if have node; then
  check 'message audio / Vorlesen (node)' node mag-room/tests/test_message_audio.cjs
else skip 'message audio' 'node not installed'; fi
check 'shell syntax' bash -c 'bash -n ops/*.sh ops/magbot-guest-run ops/magbot-agent-ctl ops/bots/*.sh ops/bots/tests/*.sh ops/ci/*.sh'
if have nginx; then check 'bots ops tests' bash ops/bots/tests/test-bots.sh
else skip 'bots ops tests' 'needs the nginx binary (nginx -t on a temp config)'; fi

# room-e2e.yml + image-paste.yml (Playwright Chromium)
if ((BROWSER)) && python3 -c 'import playwright' 2>/dev/null; then
  for t in test_room_e2e test_reactions test_numa_ui test_cards_ui test_tts test_view_tools \
           test_live_highlight test_stream_order test_image_paste test_memory_ui test_type_to_focus; do
    MAG_E2E_SHOTS="$LOG/shots" check "browser $t" python3 "mag-room/tests/$t.py"
  done
else
  skip 'browser e2e (room-e2e, image-paste, memory-ui)' 'python3 playwright not installed (pip install --user playwright && python3 -m playwright install chromium) or --no-browser'
fi

# green.yml prerequisites: the promotion itself runs as root on MAG.
skip 'green promotion (ops/ci/promote-green.sh)' 'needs root and writes /var/lib/mag-deploy; ops/tests/test-green.sh above covers its logic'
skip 'podman/e2e-bootstrap' 'needs podman/root'

printf '\n== local CI summary (logs: %s)\n' "$LOG"
printf '%s\n' "${RESULTS[@]}"
if ((FAIL)); then echo 'local CI FAIL'; exit 1; fi
echo 'local CI PASS'
