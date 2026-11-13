#!/usr/bin/env bash
# Build ONE drafter that is published only as Hugging Face weights, not as a
# GGUF (today: Noah's EAGLE-3 head, nvidia/gpt-oss-120b-Eagle3-v3):
#   build-draft.sh <bot> <kind>
# Called by fetch.sh (mag-model-fetch@<bot>-draft.service, user chris, cores
# 28-55) while it holds the file's lock, so there is one writer per file.
# The checkpoint and the target's tokenizer come pinned by commit (the
# checkpoint also by SHA256) and are converted with the converter of the
# pinned model runtime, which is the code that reads the GGUF. mag-modeld then
# checks the result against the size and SHA256 pinned in models.json like any
# other model file. Nothing is ever deleted except this script's own .part.
set -euo pipefail
bot=${1:?usage: build-draft.sh <bot> <kind>}; kind=${2:?}
CONF=${MAG_MODELS_CONF:-/usr/local/lib/mag-models/models.json}
spec=$(jq -ce --arg b "$bot" --arg k "$kind" '.bots[$b][$k] // empty' "$CONF")
q() { jq -r "$1" <<<"$spec"; }
path=$(q .path)
type=$(q '.build.type // empty')
[[ $type == eagle3 ]] || { echo "build-draft: $bot/$kind has no known build" >&2; exit 64; }
repo=$(q .build.repo); rev=$(q .build.rev); sha=$(q .build.sha256)
target=$(q .build.target); target_rev=$(q .build.target_rev); outtype=$(q '.build.outtype // "q8_0"')
[[ $rev =~ ^[a-f0-9]{40}$ && $target_rev =~ ^[a-f0-9]{40}$ && $sha =~ ^[a-f0-9]{64}$ ]] || { echo "build-draft: unpinned build" >&2; exit 64; }

REV=$(jq -r '.runtime.rev' "$CONF")
SRC=$HOME/.local/src/mag-coding-llama.cpp
[[ $(git -C "$SRC" rev-parse HEAD 2>/dev/null) == "$REV" ]] || { echo "build-draft: runtime source $SRC is not at $REV (ops/models/build-runtime.sh)" >&2; exit 75; }

work="$path.src"
mkdir -p "$work/draft" "$work/target"
get() { # <repo> <rev> <file> <dir>
  curl -fsSL --retry 5 --retry-delay 15 -C - -o "$4/$3" "https://huggingface.co/$1/resolve/$2/$3"
}
get "$repo" "$rev" config.json "$work/draft"
if ! echo "$sha  $work/draft/model.safetensors" | sha256sum -c --quiet 2>/dev/null; then
  get "$repo" "$rev" model.safetensors "$work/draft"
  echo "$sha  $work/draft/model.safetensors" | sha256sum -c --quiet || { echo "build-draft: $repo checkpoint SHA256 differs" >&2; exit 1; }
fi
# The tokenizer and the layer count of the model the drafter guesses for.
for f in config.json tokenizer.json tokenizer_config.json special_tokens_map.json generation_config.json chat_template.jinja; do
  [[ -s $work/target/$f ]] || get "$target" "$target_rev" "$f" "$work/target"
done

# Any Python with the converter's packages; else a CPU-only venv of its own.
PY=
for p in "${MAG_CONVERT_PY:-}" "$HOME/idol-mi50/venv/bin/python" "$SRC/.venv-convert/bin/python"; do
  [[ -n $p && -x $p ]] && "$p" -c 'import torch, transformers, numpy, safetensors, sentencepiece' 2>/dev/null && { PY=$p; break; }
done
if [[ -z $PY ]]; then
  python3 -m venv "$SRC/.venv-convert"
  "$SRC/.venv-convert/bin/pip" install -q -r "$SRC/requirements/requirements-convert_hf_to_gguf.txt"
  PY=$SRC/.venv-convert/bin/python
fi

rm -f "$path.part"
HF_HUB_OFFLINE=1 HF_HOME="$work/hf" "$PY" "$SRC/convert_hf_to_gguf.py" "$work/draft" \
  --target-model-dir "$work/target" --outtype "$outtype" --outfile "$path.part"
mv -f "$path.part" "$path"
echo "build-draft: $path $(stat -c %s "$path") $(sha256sum "$path" | cut -d' ' -f1)"
