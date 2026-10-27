#!/usr/bin/env bash
# Local replacement for the GitHub/Gitea workflow steps while Actions is
# blocked. Pins to NUMA1 SMT siblings 42-55 when those CPUs exist (docs/RUNNERS.md).
# One fail-fast pool: the first error kills the rest. Native cmake/ninja is
# hashed and skipped when ./build already matches. backup restore e2e runs
# only when backup/ files change. Never starts/stops/reloads mag-120, mag-llm,
# ornith or nginx. Checks that need root, podman or the GitHub API print SKIP.
#   bash ops/ci/local-ci.sh [--jobs N] [--no-browser]
set -uo pipefail
ROOT=$(cd -- "$(dirname -- "$(readlink -f "$0")")/../.." && pwd)
cd "$ROOT" || exit 2
if [[ -z ${MAG_CI_PINNED:-} && ${MAG_CI_NO_PIN:-} != 1 ]]; then
  if [[ -e /sys/devices/system/cpu/cpu42 && -e /sys/devices/system/cpu/cpu55 ]]; then
    export MAG_CI_PINNED=1
    exec taskset -c 42-55 bash "$0" "$@"
  fi
fi
# shellcheck disable=SC1091 # reap.sh is passed to shellcheck on its own in native-fast.list
source "$ROOT/ops/ci/reap.sh"
mag_ci_arm "$ROOT/ops/ci/ci_reap.py" "$@"
JOBS=${MAG_CI_JOBS:-$(nproc 2>/dev/null || echo 4)} BROWSER=1
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
set -e
LOG=$(mktemp -d /tmp/mag-local-ci.XXXXXX)
export MAG_CI_BUILD_OK="$LOG/build.ok" MAG_CI_BUILD_FAIL="$LOG/build.fail" MAG_CI_JOBS=$JOBS
WAIT="$ROOT/ops/ci/wait-native-build.sh"
have() { command -v "$1" >/dev/null 2>&1; }
skip() { printf 'SKIP  %s: %s\n' "$1" "$2"; }

list=$LOG/jobs.list
if [[ -n ${MAG_CI_LOCAL_LIST:-} ]]; then
  list=$MAG_CI_LOCAL_LIST
else
  {
    printf '%s\n' "@900 Build :: bash \"$ROOT/ops/ci/native-build.sh\""
    cat "$ROOT/ops/ci/native-fast.list"
    printf '%s\n' 'Test and workflow deadlines :: python3 ops/ci/check-timeouts.py'
    if have restic && have sqlite3; then
      printf '%s\n' 'Empty legacy Gitea directory :: bash ops/backup/tests/test-gitea-root.sh'
    else
      skip 'Empty legacy Gitea directory' 'restic/sqlite3 not installed' >&2
    fi
    if have node && have curl; then
      printf '%s\n' 'Vendor pinned Moonshine :: bash ops/vendor-speech-assets.sh build/speech-assets'
    else
      skip 'Vendor pinned Moonshine' 'node/curl not installed' >&2
    fi
    if bash "$ROOT/ops/ci/backup-affected.sh"; then
      printf '%s\n' '@240 backup restore e2e :: bash ops/backup/tests/test-backup.sh'
    else
      skip 'backup restore e2e' 'no backup/ path change' >&2
    fi
    while IFS= read -r line || [[ -n ${line:-} ]]; do
      [[ -z ${line//[[:space:]]/} || $line == \#* ]] && continue
      name=${line%% :: *}
      cmd=${line#* :: }
      printf '%s :: bash %q && %s\n' "$name" "$WAIT" "$cmd"
    done < "$ROOT/ops/ci/native-bins.list"
    python3 "$ROOT/ops/ci/run-native-suite.py" --emit-shards --jobs "$JOBS" --file mag-room/tests/test_native.py \
      | while IFS= read -r line || [[ -n ${line:-} ]]; do
          [[ -z ${line//[[:space:]]/} || $line == \#* ]] && continue
          name=${line%% :: *}
          cmd=${line#* :: }
          printf '%s :: bash %q && %s\n' "$name" "$WAIT" "$cmd"
        done
    while IFS= read -r line || [[ -n ${line:-} ]]; do
      [[ -z ${line//[[:space:]]/} || $line == \#* ]] && continue
      name=${line%% :: *}
      cmd=${line#* :: }
      printf '%s :: bash %q && %s\n' "$name" "$WAIT" "$cmd"
    done < "$ROOT/ops/ci/native-rest.list"
    printf '%s\n' "vm archive :: bash $WAIT && python3 mag-room/tests/test_vm_archive.py"
    printf '%s\n' "retire DeepSeek :: bash $WAIT && bash ops/tests/test-retire-deepseek.sh"
    if have gitleaks; then
      printf '%s\n' 'gitleaks (no secrets) :: gitleaks detect --no-banner --redact --source .'
    else
      printf '%s\n' "no-secrets grep (gitleaks not installed) :: bash $ROOT/ops/ci/no-secrets.sh"
    fi
    if have go; then
      printf '%s\n' "browser WebRTC gateway (go test/build) :: cd mag-room/webrtc && go test -mod=mod -timeout 60s ./... && go build -mod=mod -o ${LOG}/mag-browser-webrtc ."
    else
      skip 'browser WebRTC gateway' 'go not installed' >&2
    fi
    if have node; then
      printf '%s\n' 'message audio / Vorlesen (node) :: node mag-room/tests/test_message_audio.cjs'
    else
      skip 'message audio' 'node not installed' >&2
    fi
    printf '%s\n' 'shell syntax :: bash -n ops/*.sh ops/magbot-guest-run ops/magbot-agent-ctl ops/bots/*.sh ops/bots/tests/*.sh ops/ci/*.sh'
    if have nginx; then
      printf '%s\n' 'bots ops tests :: bash ops/bots/tests/test-bots.sh'
    else
      skip 'bots ops tests' 'needs the nginx binary (nginx -t on a temp config)' >&2
    fi
    if ((BROWSER)) && python3 -c 'import playwright' 2>/dev/null; then
      printf '%s\n' "room-e2e :: bash $WAIT && MAG_E2E_SHOTS=$LOG/shots python3 mag-room/tests/test_room_e2e.py"
      for t in test_reactions test_numa_ui test_cards_ui test_tts test_view_tools \
               test_live_highlight test_stream_order test_image_paste test_memory_ui test_type_to_focus; do
        printf '%s\n' "browser $t :: bash $WAIT && MAG_E2E_SHOTS=$LOG/shots python3 mag-room/tests/$t.py"
      done
    else
      skip 'browser e2e (room-e2e, image-paste, memory-ui)' \
        'python3 playwright not installed (pip install --user playwright && python3 -m playwright install chromium) or --no-browser' >&2
    fi
    skip 'tailscale egress nft regression' 'needs root and modifies nft (github-hosted only)' >&2
    skip 'nginx -t of the private site' 'needs sudo nginx (github-hosted only)' >&2
    skip 'green promotion (ops/ci/promote-green.sh)' 'needs root and writes /var/lib/mag-deploy; ops/tests/test-green.sh above covers its logic' >&2
    skip 'podman/e2e-bootstrap' 'needs podman/root' >&2
  } >"$list"
fi

if [[ ${MAG_CI_LIST_ONLY:-0} == 1 ]]; then
  cat "$list"
  exit 0
fi

echo "local CI pin=${MAG_CI_PINNED:-0} jobs=$JOBS logs=$LOG"
set +e
bash "$ROOT/ops/ci/run-parallel.sh" --jobs "$JOBS" --timeout 180 <"$list"
st=$?
set -e
if ! mag_ci_kill_worktree_magroom; then
  echo 'local CI FAIL leftover magroom' >&2
  exit 1
fi
printf '\n== local CI summary (logs: %s)\n' "$LOG"
if [[ $st -ne 0 ]]; then
  echo 'local CI FAIL'
  exit "$st"
fi
echo 'local CI PASS'
