#!/usr/bin/env bash
# One-time retirement of the old DeepSeek V4.1-Flash Q2_K model.
# Explicitly retains resident DeepSeek V4 Q4 and Qwen IQ4_XS.
set -euo pipefail
[[ $EUID == 0 ]] || { echo 'root required' >&2; exit 1; }
target=/home/chris/models/DeepSeek-V4.1-Flash-1017-Q2_K
if [[ ! -e "$target" && ! -L "$target" ]]; then
  echo 'Legacy V4.1 model already absent'
  exit 0
fi
[[ -d "$target" && ! -L "$target" &&
   $(realpath -e -- "$target") == "$target" ]] || {
  echo 'Refusing to delete an unexpected path or symlink' >&2; exit 4;
}
# An old model must not be removed while any process maps its weights.
for proc in /proc/[0-9]*; do
  [[ -r "$proc/cmdline" ]] || continue
  args=$(tr '\0' ' ' < "$proc/cmdline" 2>/dev/null || true)
  if [[ $args == *"$target"* ]]; then
    echo "Legacy V4.1 model still in use by PID $(basename "$proc"); not deleting" >&2
    exit 4
  fi
done
echo "Retiring obsolete model: $target"
rm -rf --one-file-system -- "$target"
[[ ! -e "$target" ]] || { echo 'Legacy model deletion incomplete' >&2; exit 4; }
echo 'Legacy DeepSeek V4.1 removed; Q4 and Qwen untouched'
