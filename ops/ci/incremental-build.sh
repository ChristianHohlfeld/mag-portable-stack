#!/usr/bin/env bash
# Incremental native build for the merge gate. Never deletes build/ or the
# persistent cache. The mag runner wipes work/ every run and creates a new
# ~/ci-local/wt/prN-<sha4> per PR, so ccache lives in a shared directory
# (default ~/.cache/mag-ci/ccache) and the ninja tree is keyed by source
# path. A leftover CMakeCache from another worktree is dropped (metadata
# only; object files stay) so cmake can configure this checkout.
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

mag_ci_src_key() {
  printf '%s' "$1" | sha256sum | cut -c1-16
}

mag_ci_cache_source() {
  [[ -f $1/CMakeCache.txt ]] || return 0
  sed -n 's/^CMAKE_HOME_DIRECTORY:INTERNAL=//p' "$1/CMakeCache.txt" | head -n 1
}

# Drop generator state so a new CMAKE_HOME_DIRECTORY can configure. Object
# files stay; the shared ccache covers any compile that ninja repeats.
mag_ci_drop_cmake_state() {
  local dir=$1
  rm -f -- "$dir/CMakeCache.txt" "$dir/CMakeCache.txt.tmp"
  if [[ -d $dir/CMakeFiles ]]; then
    find "$dir/CMakeFiles" -mindepth 1 -delete
  fi
}

CACHE=$(mag_ci_cache_root)
# Always keep ccache next to the trees. An inherited CCACHE_DIR inside
# the wiped work/ tree (or a leftover ~/.cache/ccache) is ignored.
export CCACHE_DIR="$CACHE/ccache"
# One ninja tree per source path so wt/pr163-72e0 and wt/pr154-3f89 do not
# share CMAKE_HOME_DIRECTORY. The old $CACHE/build leftover is never reused.
PERSIST=$CACHE/trees/$(mag_ci_src_key "$ROOT")

# --print-cache must not retarget ./build (tests and a live checkout).
if [[ ${1:-} == --print-cache ]]; then
  mkdir -p "$CCACHE_DIR" "$PERSIST"
  printf 'MAG_CI_CACHE=%s\nCCACHE_DIR=%s\nTREE=%s\nBUILD=%s\n' \
    "$CACHE" "$CCACHE_DIR" "$PERSIST" \
    "$(readlink -f "$ROOT/build" 2>/dev/null || printf '%s\n' "$PERSIST")"
  exit 0
fi

# A missing or already-linked ./build follows this worktree's tree, including
# a leftover broken symlink after a wiped work/. A real directory in a
# developer checkout is left alone.
mkdir -p "$CCACHE_DIR" "$PERSIST"
if [[ -L $ROOT/build || ! -e $ROOT/build ]]; then
  ln -sfn "$PERSIST" "$ROOT/build"
fi

build_dir=$ROOT/build
if [[ -f $build_dir/CMakeCache.txt ]]; then
  src=$(mag_ci_cache_source "$build_dir")
  if [[ -n $src && $src != "$ROOT" ]]; then
    mag_ci_drop_cmake_state "$build_dir"
  fi
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
fi

if ((configure)); then
  cmake -S . -B build -G Ninja -DCMAKE_BUILD_TYPE=Release "${launch[@]}"
fi
cmake --build build -j "$jobs"
