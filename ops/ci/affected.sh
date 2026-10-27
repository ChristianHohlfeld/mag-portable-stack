#!/usr/bin/env bash
# Decide whether a CI suite must run for this diff.
# Exit 0 = run it. Exit 1 = no matching path changed.
# A full run (exit 0 for every suite) happens when MAG_CI_BASE is unset,
# equals MAG_CI_SHA (the Gitea bridge does this on main), MAG_CI_FULL=1,
# or the diff cannot be read. gitleaks and secrets always run.
#
#   ops/ci/affected.sh native
#   ops/ci/affected.sh --paths native
#   ops/ci/affected.sh --workflow native
#   ops/ci/affected.sh --files native < file-list
set -euo pipefail
mode=run
if [[ ${1:-} == --paths || ${1:-} == --workflow || ${1:-} == --files ]]; then
  mode=${1#--}
  shift
fi
suite=${1:?suite}
if [[ $mode == files ]]; then
  MAG_AFFECTED_FILES=$(cat)
  export MAG_AFFECTED_FILES
fi
exec python3 - "$mode" "$suite" <<'PY'
import os, re, subprocess, sys
mode, suite = sys.argv[1], sys.argv[2]
PATHS = {
    "native": [
        "mag-room/**",
        "CMakeLists.txt",
        "mag_deterministic_engine.cpp",
        "test_mag_deterministic_engine.py",
        "ops/**",
        "devices/**",
        ".github/workflows/native.yml",
        ".gitea/workflows/mag-ci.yml",
    ],
    "pages": [
        "ops/pages/**",
        "mag-room/native/pages.hpp",
        "mag-room/native/pages_tools.hpp",
        "mag-room/tests/test_pages.cpp",
        ".github/workflows/pages-native.yml",
    ],
    "nginx": [
        "ops/private-access/**",
        "ops/nginx/**",
        "mag-room/native/tailscale_auth.hpp",
        "mag-room/tests/test_private_nginx.py",
        "mag-room/tests/test_whois_deploy.py",
        ".github/workflows/private-nginx.yml",
    ],
    "recovery": [
        "ops/backup/**",
        "ops/deploy.sh",
        "ops/deploy-guard.sh",
        ".github/workflows/backup.yml",
    ],
    "room": [
        "mag-room/web/**",
        "mag-room/tests/test_room_e2e.py",
        "mag-room/tests/test_room_control.py",
        "mag-room/tests/test_reactions.py",
        "mag-room/tests/test_numa_ui.py",
        "mag-room/tests/test_cards_ui.py",
        "mag-room/tests/test_tts.py",
        "mag-room/tests/test_view_tools.py",
        "mag-room/tests/test_live_highlight.py",
        "mag-room/tests/test_stream_order.py",
        ".github/workflows/room-e2e.yml",
    ],
    "chromium": [
        "mag-room/web/**",
        "mag-room/tests/test_image_paste.py",
        "mag-room/tests/test_memory_ui.py",
        "mag-room/tests/test_type_to_focus.py",
        ".github/workflows/image-paste.yml",
    ],
    "speech": [
        "mag-room/static/speech-ui.js",
        "mag-room/static/message-audio.js",
        "mag-room/static/speech-capture.js",
        "mag-room/web/sw.js",
        "mag-room/tests/test_speech*.py",
        "mag-room/tests/test_message_audio.cjs",
        "mag-room/tests/test_piper_warmup.cjs",
        "ops/vendor-speech-assets.sh",
        "ops/nginx/mag-speech.inc",
        ".github/workflows/speech.yml",
    ],
    "e2e-bootstrap": [
        "ops/bootstrap.sh",
        "ops/bootstrap-voice.sh",
        "ops/tests/e2e-bootstrap.sh",
        ".github/workflows/e2e-bootstrap.yml",
    ],
}
WORKFLOW = {
    "native": ".github/workflows/native.yml",
    "pages": ".github/workflows/pages-native.yml",
    "nginx": ".github/workflows/private-nginx.yml",
    "recovery": ".github/workflows/backup.yml",
    "room": ".github/workflows/room-e2e.yml",
    "chromium": ".github/workflows/image-paste.yml",
    "speech": ".github/workflows/speech.yml",
    "e2e-bootstrap": ".github/workflows/e2e-bootstrap.yml",
}
ALWAYS = {"gitleaks", "secrets"}

def github_match(path, pattern):
    # GitHub path filters: * and ? stay inside one segment, ** crosses '/'.
    i, rx = 0, "^"
    while i < len(pattern):
        if pattern.startswith("**", i):
            if pattern[i + 2:i + 3] == "/":
                rx += "(?:.*/)?"
                i += 3
            else:
                rx += ".*"
                i += 2
        elif pattern[i] == "*":
            rx += "[^/]*"
            i += 1
        elif pattern[i] == "?":
            rx += "[^/]"
            i += 1
        else:
            rx += re.escape(pattern[i])
            i += 1
    return re.match(rx + "$", path) is not None

def matched(files, patterns):
    return any(github_match(path, pat) for path in files for pat in patterns)

def die(msg):
    print(msg, file=sys.stderr)
    sys.exit(2)

if mode == "paths":
    if suite not in PATHS:
        die(f"no path list for {suite}")
    print("\n".join(PATHS[suite]))
    sys.exit(0)
if mode == "workflow":
    if suite not in WORKFLOW:
        die(f"no workflow for {suite}")
    print(WORKFLOW[suite])
    sys.exit(0)
if suite in ALWAYS:
    sys.exit(0)
if suite not in PATHS:
    die(f"unknown suite {suite}")
base = os.environ.get("MAG_CI_BASE", "")
sha = os.environ.get("MAG_CI_SHA", "")
full = os.environ.get("MAG_CI_FULL", "")
if mode == "files":
    files = [line.strip() for line in os.environ.get("MAG_AFFECTED_FILES", "").splitlines() if line.strip()]
    sys.exit(0 if matched(files, PATHS[suite]) else 1)
if full == "1" or not base or not sha or base == sha:
    sys.exit(0)
source = os.environ.get("MAG_CI_SOURCE", "")
if not source:
    source = "/work/source" if os.path.isdir("/work/source/.git") else ""
if not source:
    sys.exit(0)
try:
    diff = subprocess.run(
        ["git", "-C", source, "diff", "--name-only", base, sha],
        check=False, capture_output=True, text=True,
    )
except OSError:
    sys.exit(0)
if diff.returncode != 0:
    sys.exit(0)
files = [line for line in diff.stdout.splitlines() if line]
sys.exit(0 if matched(files, PATHS[suite]) else 1)
PY
