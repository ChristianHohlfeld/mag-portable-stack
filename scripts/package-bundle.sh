#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_DIR="${1:-$ROOT/dist}"
STAMP="$(date +%Y%m%d-%H%M%S)"
ARCHIVE="$OUT_DIR/mag-portable-stack-$STAMP.tar.zst"

mkdir -p "$OUT_DIR"

echo "Packing bundle: $ARCHIVE"
tar \
  --exclude=".git" \
  --exclude="dist" \
  --exclude="*.pyc" \
  --exclude="__pycache__" \
  -I 'zstd -T0 -19' \
  -cf "$ARCHIVE" \
  -C "$(dirname "$ROOT")" \
  "$(basename "$ROOT")"

echo "Done: $ARCHIVE"
