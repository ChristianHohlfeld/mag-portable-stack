#!/usr/bin/env bash
# Original speech.yml / speech: same tests, one shared native build inside a disposable VM.
set -euo pipefail
export RUNNER_ENVIRONMENT=github-hosted MAG_E2E_SHOTS=/work/screenshots
export GITHUB_REPOSITORY=ChristianHohlfeld/mag-portable-stack
test -f CMakeLists.txt
test -d .git
printf "\n== %s\n" "Compile native MAG for browser tests"
cmake -S . -B build -G Ninja -DCMAKE_BUILD_TYPE=Release && cmake --build build --target magroom -j 2
printf "\n== %s\n" "Vendor pinned terminal browser assets"
bash ops/vendor-terminal-assets.sh build/terminal-assets
test -s build/terminal-assets/xterm.js
test -s build/terminal-assets/xterm-addon-fit.js
test -s build/terminal-assets/xterm.css

printf "\n== %s\n" "Syntax and fake-model Playwright regressions"
node --check mag-room/static/speech-ui.js
node --check mag-room/static/message-audio.js
node mag-room/tests/test_message_audio.cjs
node mag-room/tests/test_piper_warmup.cjs
node --check mag-room/static/speech-capture.js
node --check mag-room/web/sw.js
bash -n ops/vendor-speech-assets.sh ops/deploy.sh
python3 -m unittest discover -s mag-room/tests -p test_speech.py -v

printf "\n== %s\n" "Vendor pinned Moonshine German WASM and model"
bash ops/vendor-speech-assets.sh build/speech-assets
printf "\n== %s\n" "Playwright with actual WASM and German model"
python3 -m unittest discover -s mag-room/tests -p test_speech_real.py -v
printf "\n== %s\n" "Reject unapproved removal of live files"
git show-ref --verify refs/remotes/origin/main >/dev/null
# #21 deliberately retires precisely two Python whois artifacts,
# and only with the native C++ LocalAPI replacement in place.
while IFS= read -r removed; do
  [ -n "$removed" ] || continue
  case "$removed" in
    ops/private-access/tailscale-whois.py|ops/private-access/mag-whois.service)
      test -s mag-room/native/tailscale_auth.hpp
      grep -Fq 'proxy_pass http://127.0.0.1:8766/_internal/tailscale/whois;' ops/private-access/mag-room.nginx.conf
      ;;
    ops/deepseek/both-sockets.sh|ops/mag-120.d/30-multibot.conf)
      # Retired: no executable compatibility paths remain.
      test ! -e "$removed"
      ;;
    .gitea/workflows/ci.yml)
      # Retired host-mode Gitea workflow. The isolated runner replaces it.
      test ! -e "$removed"
      test -s .gitea/workflows/mag-ci.yml
      grep -Fq 'runs-on: mag-ci-vm' .gitea/workflows/mag-ci.yml
      ! grep -Eq '^[[:space:]]*runs-on:[[:space:]]*mag[[:space:]]*$' .gitea/workflows/mag-ci.yml
      ;;
    *)
      echo "Unexpected deletion in speech integration: $removed" >&2
      exit 1
      ;;
  esac
done < <(git diff --name-only --diff-filter=D origin/main...HEAD)

