#!/usr/bin/env bash
# Restore Q4+IQ3 model shards on NEW hardware; never start/stop any model.
# No model bytes are stored in backup or git.
set -euo pipefail
ROOT=$(cd -- "$(dirname -- "$0")/../.." && pwd)
ACTION=${1:-}
SELECT=${2:-all}
case "$ACTION" in download|verify) ;; *) echo "Usage: $0 download|verify [q4|iq3|all]" >&2;exit 2;; esac
case "$SELECT" in q4|iq3|all) ;; *) exit 2;; esac
if [[ "$ACTION" == download ]];then
  for u in mag-llm.service mag-llm-iq3.service;do
    if systemctl is-active --quiet "$u" 2>/dev/null;then
      echo "Model $u is live; never download over a serving model" >&2;exit 4
    fi
  done
  command -v curl >/dev/null || exit 4
fi
download_and_verify() {
  local flavor=$1 manifest=$2 path=$3 prefix=$4
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
    # The resolved HTTP object is checked against the pinned SHA, not trusted by filename.
    local url="https://huggingface.co/unsloth/DeepSeek-V4-Flash-0731-GGUF/resolve/main/$prefix/$file?download=true"
    local part="$path/$file.part"
    echo "Downloading pinned shard: $file"
    curl --fail --location --retry 6 --retry-all-errors --connect-timeout 20 \
      --continue-at - --output "$part" "$url" || { echo "Download failed, partial kept" >&2;return 4; }
    (cd "$path"; echo "$sha  $file.part" | sha256sum --check --status) || {
      echo "SHA mismatch; no model replaced: $file" >&2;return 4;
    }
    mv -f -- "$part" "$path/$file"
  done < "$manifest"
  (cd "$path"; sha256sum --check --strict --status "$manifest") || { echo "SHA verification failed" >&2;return 4; }
  if [[ "$flavor" == iq3 ]];then
    # IQ3 runtime's verify-iq3.sh expects this exact file, not GGUF backups.
    sed '/^#/d' "$manifest" > "$path/SHA256SUMS"
  fi
  echo "Verified all $flavor model splits"
}
if [[ "$SELECT" == q4 || "$SELECT" == all ]];then
 download_and_verify q4 "$ROOT/ops/backup/models.sha256" \
  "${MAG_Q4_MODEL_DIR:-/home/chris/models/deepseek-v4}" UD-Q4_K_XL
fi
if [[ "$SELECT" == iq3 || "$SELECT" == all ]];then
 download_and_verify iq3 "$ROOT/ops/backup/models-iq3.sha256" \
  "${MAG_IQ3_MODEL_DIR:-/home/chris/models/deepseek-v4-UD-IQ3_XXS}" UD-IQ3_XXS
fi
