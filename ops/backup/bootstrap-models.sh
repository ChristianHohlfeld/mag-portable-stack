#!/usr/bin/env bash
# Restore the Ornith model on NEW hardware; never start/stop any model.
# DeepSeek (Q4/IQ3) is retired: it is neither downloaded nor verified any more
# (ops/deepseek/ is archived). Rollback model Qwen: ops/qwen/README.md.
# No model bytes are stored in backup or git.
set -euo pipefail
ROOT=$(cd -- "$(dirname -- "$0")/../.." && pwd)
ACTION=${1:-}
SELECT=${2:-all}
case "$ACTION" in download|verify) ;; *) echo "Usage: $0 download|verify [ornith|all]" >&2;exit 2;; esac
case "$SELECT" in ornith|all) ;; q4|iq3) echo 'DeepSeek is retired; nothing to restore' >&2;exit 2;; *) exit 2;; esac
if [[ "$ACTION" == download ]];then
  for u in mag-llm-ornith-node@0.service mag-llm-ornith-node@1.service;do
    if systemctl is-active --quiet "$u" 2>/dev/null;then
      echo "Model $u is live; never download over a serving model" >&2;exit 4
    fi
  done
  command -v curl >/dev/null || exit 4
fi
download_and_verify() {
  local flavor=$1 manifest=$2 path=$3 url_base=$4
  [[ -r "$manifest" && ! -L "$manifest" ]] || { echo "Invalid manifest: $manifest" >&2;return 4; }
  mkdir -p -- "$path"
  while read -r sha file;do
    [[ -n "$sha" && "$sha" != \#* ]] || continue
    [[ "$sha" =~ ^[a-f0-9]{64}$ && "$file" =~ ^[a-zA-Z0-9_.-]+[.]gguf$ ]] || { echo "Invalid manifest line" >&2;return 4; }
    if [[ -f "$path/$file" ]] && (cd "$path" && echo "$sha  $file" | sha256sum -c --status);then
      echo "Verified existing: $file"
      continue
    fi
    [[ "$ACTION" == download ]] || { echo "Missing or wrong SHA256: $file" >&2;return 4; }
    [[ -n "$url_base" ]] || { echo "Set MAG_ORNITH_MODEL_URL (directory URL of $file) to download" >&2;return 4; }
    # The resolved HTTP object is checked against the pinned SHA, not trusted by filename.
    local url="$url_base/$file"
    local part="$path/$file.part"
    echo "Downloading pinned file: $file"
    curl --fail --location --retry 6 --retry-all-errors --connect-timeout 20 \
      --continue-at - --output "$part" "$url" || { echo "Download failed, partial kept" >&2;return 4; }
    (cd "$path"; echo "$sha  $file.part" | sha256sum --check --status) || {
      echo "SHA mismatch; no model replaced: $file" >&2;return 4;
    }
    mv -f -- "$part" "$path/$file"
  done < "$manifest"
  (cd "$path"; sha256sum --check --strict --status "$manifest") || { echo "SHA verification failed" >&2;return 4; }
  echo "Verified all $flavor model files"
}
download_and_verify ornith "$ROOT/ops/ornith/model.sha256" \
  "${MAG_ORNITH_MODEL_DIR:-/home/chris/models/ornith-1.5-35b-a3b}" "${MAG_ORNITH_MODEL_URL:-}"
