#!/usr/bin/env bash
# Incremental native build for the merge gate. Never deletes build/ or the
# persistent cache. The mag runner wipes work/ every run, so ccache and the
# ninja tree live outside the checkout (default ~/.cache/mag-ci).
# mag-test-timeout: 180
set -euo pipefail
ROOT=$(cd -- "$(dirname -- "$(readlink -f "$0")")/../.." && pwd)
cd "$ROOT"
uid=$(id -u)

mag_ci_cache_ok() {
  local p=$1
  [[ -n $p ]] || return 1
  [[ $p != "$ROOT" && $p != "$ROOT"/* ]] || return 1
  [[ $p != /work && $p != /work/* ]] || return 1
  [[ $p != */work && $p != */work/* ]] || return 1
  return 0
}

mag_ci_cache_root() {
  local cand=${MAG_CI_CACHE:-}
  if mag_ci_cache_ok "$cand"; then
    printf '%s\n' "$cand"
    return
  fi
  cand=${HOME:-}/.cache/mag-ci
  if mag_ci_cache_ok "$cand"; then
    printf '%s\n' "$cand"
    return
  fi
  printf '%s\n' "/var/tmp/mag-ci-${uid}"
}

CACHE=$(mag_ci_cache_root)
# Always keep ccache next to the ninja tree. An inherited CCACHE_DIR inside
# the wiped work/ tree (or a leftover ~/.cache/ccache) is ignored.
export CCACHE_DIR="$CACHE/ccache"
PERSIST=$CACHE/build

# --print-cache must not retarget ./build (tests and a live checkout).
if [[ ${1:-} == --print-cache ]]; then
  mkdir -p "$CCACHE_DIR" "$PERSIST"
  printf 'MAG_CI_CACHE=%s\nCCACHE_DIR=%s\nBUILD=%s\n' \
    "$CACHE" "$CCACHE_DIR" "$(readlink -f "$ROOT/build" 2>/dev/null || printf '%s\n' "$PERSIST")"
  exit 0
fi

# A missing or already-linked ./build follows the persistent tree, including
# a leftover broken symlink after a wiped work/. A real directory in a
# developer checkout is left alone.
mkdir -p "$CCACHE_DIR" "$PERSIST"
if [[ -L $ROOT/build || ! -e $ROOT/build ]]; then
  ln -sfn "$PERSIST" "$ROOT/build"
fi

if [[ ${1:-} == --prepare ]]; then
  exit 0
fi

if [[ ${1:-} == --jobs ]]; then
  jobs=${2:?}
else
  jobs=${MAG_CI_JOBS:-$(nproc 2>/dev/null || echo 2)}
fi

launch=()
if command -v ccache >/dev/null; then
  launch+=(-DCMAKE_C_COMPILER_LAUNCHER=ccache -DCMAKE_CXX_COMPILER_LAUNCHER=ccache)
fi

configure=0
if [[ ! -f build/CMakeCache.txt ]]; then
  configure=1
  # First configure only. A clang-default host uses g++-13 when present so
  # libstdc++ matches the MAG builders. Existing caches keep their compiler.
  if command -v c++ >/dev/null && c++ --version 2>/dev/null | grep -qi clang; then
    if command -v g++-13 >/dev/null && command -v gcc-13 >/dev/null; then
      launch+=(-DCMAKE_C_COMPILER=gcc-13 -DCMAKE_CXX_COMPILER=g++-13)
    fi
  fi
elif [[ CMakeLists.txt -nt build/CMakeCache.txt ]]; then
  configure=1
else
  src=$(sed -n 's/^CMAKE_HOME_DIRECTORY:INTERNAL=//p' build/CMakeCache.txt | head -n 1)
  if [[ -n $src && $src != "$ROOT" ]]; then
    configure=1
  fi
fi

if ((configure)); then
  cmake -S . -B build -G Ninja -DCMAKE_BUILD_TYPE=Release "${launch[@]}"
fi
cmake --build build -j "$jobs"
