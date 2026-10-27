#!/usr/bin/env bash
# Original room-e2e.yml / room: same tests, one shared native build inside a disposable VM.
set -euo pipefail
export RUNNER_ENVIRONMENT=github-hosted MAG_E2E_SHOTS=/work/screenshots
export GITHUB_REPOSITORY=ChristianHohlfeld/mag-portable-stack
test -f CMakeLists.txt
test -d .git
printf "\n== %s\n" "Build the native room"
cmake -S . -B build -G Ninja -DCMAKE_BUILD_TYPE=Release && cmake --build build --target magroom -j 2
printf "\n== %s\n" "Stop, user priority and queue against a fake model"
python3 mag-room/tests/test_room_control.py
printf "\n== %s\n" "Browser e2e (composer Stop/Esc, queue, run card, menus, mobile drawers)"
python3 mag-room/tests/test_room_e2e.py
printf "\n== %s\n" "Message popover and NUMA selector (phone portrait/landscape, desktop)"
python3 mag-room/tests/test_reactions.py
python3 mag-room/tests/test_numa_ui.py
python3 mag-room/tests/test_cards_ui.py
python3 mag-room/tests/test_tts.py
python3 mag-room/tests/test_view_tools.py
python3 mag-room/tests/test_live_highlight.py
python3 mag-room/tests/test_stream_order.py

