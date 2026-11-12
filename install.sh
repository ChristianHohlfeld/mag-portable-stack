#!/usr/bin/env bash
# MAG on a fresh server: one entry point. See docs/INSTALL.md.
#   git clone https://github.com/ChristianHohlfeld/mag-portable-stack.git ~/mag-stack
#   cd ~/mag-stack && sudo ./install.sh            # all phases, in order
#   sudo ./install.sh --list | --only PHASE | --from PHASE | --again PHASE
exec bash "$(cd -- "$(dirname -- "$0")" && pwd)/ops/install/run.sh" "$@"
