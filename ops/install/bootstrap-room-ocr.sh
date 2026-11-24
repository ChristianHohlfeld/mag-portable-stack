#!/usr/bin/env bash
# Install the PDF tools and language data needed to run the room suite on Ubuntu.
set -euo pipefail
here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
mapfile -t packages < "$here/room-ocr-packages.txt"
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "${packages[@]}"
