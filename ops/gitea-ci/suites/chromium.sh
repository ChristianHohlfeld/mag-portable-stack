#!/usr/bin/env bash
# Original image-paste.yml / chromium: same tests, one shared native build inside a disposable VM.
set -euo pipefail
export RUNNER_ENVIRONMENT=github-hosted MAG_E2E_SHOTS=/work/screenshots
export GITHUB_REPOSITORY=ChristianHohlfeld/mag-portable-stack
test -f CMakeLists.txt
test -d .git
printf "\n== %s\n" "Test screenshot paste at 375 390 412 and desktop"
python mag-room/tests/test_image_paste.py
printf "\n== %s\n" "Memories dialog UI (mobile and desktop)"
python mag-room/tests/test_memory_ui.py
printf "\n== %s\n" "Type anywhere goes to the composer (desktop and phones)"
python mag-room/tests/test_type_to_focus.py
