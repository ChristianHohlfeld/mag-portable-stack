#!/usr/bin/env bash
# Hash the native C++ inputs and skip cmake/ninja when ./build already matches.
# Extra arguments are cmake --build --target names (default: the full tree).
#   MAG_CI_FORCE_BUILD=1   rebuild even when the stamp matches
#   MAG_CI_BUILD_DIR       build directory (default: $ROOT/build, prepared
#                          as the per-worktree tree under ~/.cache/mag-ci)
#   MAG_CI_JOBS            ninja parallelism
set -euo pipefail
ROOT=$(cd -- "$(dirname -- "$(readlink -f "$0")")/../.." && pwd)
cd "$ROOT"
# shellcheck source=../build-targets.sh disable=SC1091
source "$ROOT/ops/build-targets.sh"
# Default ./build is this source path's persistent tree. Tests that set
# MAG_CI_BUILD_DIR keep an isolated directory.
if [[ -z ${MAG_CI_BUILD_DIR:-} ]]; then
  bash "$ROOT/ops/ci/incremental-build.sh" --prepare
fi
dir=${MAG_CI_BUILD_DIR:-$ROOT/build}
# A leftover CMakeCache from another ~/ci-local/wt/prN-<sha4> aborts cmake
# immediately. Drop generator state only; objects stay for ccache/ninja.
if [[ -f $dir/CMakeCache.txt ]]; then
  src=$(sed -n 's/^CMAKE_HOME_DIRECTORY:INTERNAL=//p' "$dir/CMakeCache.txt" | head -n 1)
  if [[ -n $src && $src != "$ROOT" ]]; then
    rm -f -- "$dir/CMakeCache.txt" "$dir/CMakeCache.txt.tmp"
    if [[ -d $dir/CMakeFiles ]]; then
      find "$dir/CMakeFiles" -mindepth 1 -delete
    fi
  fi
fi
jobs=${MAG_CI_JOBS:-$(nproc 2>/dev/null || echo 2)}
stamp="$dir/.mag-native-hash"
ready=${MAG_CI_BUILD_OK:-}
failf=${MAG_CI_BUILD_FAIL:-}
mark_fail() { if [[ -n $failf ]]; then : >"$failf"; fi; }
mark_ok() { if [[ -n $ready ]]; then : >"$ready"; fi; }
on_exit() {
  local st=$?
  if [[ $st -ne 0 ]]; then
    mark_fail
  fi
  exit "$st"
}
trap on_exit EXIT

hash_inputs() {
  python3 - "$ROOT" <<'PY'
import hashlib, sys
from pathlib import Path
root = Path(sys.argv[1])
paths = []
for rel in (
    "CMakeLists.txt",
    "mag_deterministic_engine.cpp",
    "ops/gitea-ci/bridge.cpp",
    "ops/gitea-ci/guard.cpp",
    "ops/bots/publishd.cpp",
):
    path = root / rel
    if path.is_file():
        paths.append(path)
for folder in ("mag-room/native", "devices"):
    base = root / folder
    if base.is_dir():
        for path in base.rglob("*"):
            if path.is_file() and path.suffix in {".cpp", ".hpp", ".h", ".c", ".cc", ".md", ".in"}:
                paths.append(path)
tests = root / "mag-room/tests"
if tests.is_dir():
    paths.extend(tests.glob("*.cpp"))
digest = hashlib.sha256()
digest.update(b"Release\n")
for path in sorted(set(paths), key=lambda item: item.as_posix()):
    digest.update(path.relative_to(root).as_posix().encode())
    digest.update(b"\0")
    digest.update(path.read_bytes())
    digest.update(b"\0")
print(digest.hexdigest())
PY
}

wanted=("$@")
if ((${#wanted[@]} == 0)); then
  wanted=("${MAG_BUILD_TARGETS[@]}" s23ctl
    test-pho-codec test-guest-pty test-browser-recovery test-jobs-mcp
    test-pages-native test-browser-events test-guest-description
    test-guest-curl test-large-stdin test-gitea-account)
fi

have_bins() {
  local name
  for name in "${wanted[@]}"; do
    [[ -x $dir/$name ]] || return 1
  done
  return 0
}

current=$(hash_inputs)
if [[ ${MAG_CI_FORCE_BUILD:-0} != 1 && -f $stamp && -d $dir ]] && \
   [[ $(cat "$stamp") == "$current" ]] && have_bins; then
  echo "native-build: cache hit ${current:0:12}"
  for name in "${wanted[@]}"; do chmod +x "$dir/$name" 2>/dev/null || true; done
  mark_ok
  trap - EXIT
  exit 0
fi

launch=()
if command -v ccache >/dev/null; then
  if [[ -z ${MAG_CI_BUILD_DIR:-} ]]; then
    eval "$(bash "$ROOT/ops/ci/incremental-build.sh" --print-cache | sed -n 's/^CCACHE_DIR=/export CCACHE_DIR=/p')"
  else
    export CCACHE_DIR="${CCACHE_DIR:-$HOME/.cache/ccache}"
  fi
  mkdir -p "$CCACHE_DIR"
  launch+=(-DCMAKE_C_COMPILER_LAUNCHER=ccache -DCMAKE_CXX_COMPILER_LAUNCHER=ccache)
fi
cmake -S "$ROOT" -B "$dir" -G Ninja -DCMAKE_BUILD_TYPE=Release "${launch[@]}"
if ((${#@} == 0)); then
  cmake --build "$dir" -j "$jobs"
else
  cmake --build "$dir" -j "$jobs" --target "$@"
fi
mkdir -p "$dir"
printf '%s\n' "$current" >"$stamp"
for name in "${wanted[@]}"; do chmod +x "$dir/$name" 2>/dev/null || true; done
echo "native-build: wrote ${current:0:12}"
mark_ok
trap - EXIT
