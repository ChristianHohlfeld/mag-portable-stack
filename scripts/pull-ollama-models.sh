#!/usr/bin/env bash
set -euo pipefail

MODELS=("$@")
if [ "${#MODELS[@]}" -eq 0 ]; then
  MODELS=("qwen3-fast:latest")
fi

for model in "${MODELS[@]}"; do
  echo "Pulling Ollama model: $model"
  docker exec mag-ollama ollama pull "$model"
done

echo "Done"
