#!/bin/bash
set -euo pipefail
export ENABLE_IMAGE_GENERATION=true
exec /home/chris/.local/bin/open-webui serve --host 0.0.0.0 --port 3000 "$@"
