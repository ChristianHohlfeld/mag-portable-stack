#!/usr/bin/env bash
# mag-test-timeout: 60
if [ -z "${MAG_TEST_DEADLINE_ARMED:-}" ]; then
  export MAG_TEST_DEADLINE_ARMED=1
  exec timeout --kill-after=5s 60s bash "$0" "$@"
fi
# Hash stamp skips cmake when binaries already match.
set -euo pipefail
root=$(cd -- "$(dirname "$0")/../../.." && pwd)
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/bin" "$tmp/src/mag-room/native" "$tmp/src/mag-room/tests" "$tmp/src/devices" \
  "$tmp/src/ops/gitea-ci" "$tmp/src/ops/bots" "$tmp/src/ops/ci" "$tmp/src/ops"
printf 'cmake_minimum_required(VERSION 3.20)\nproject(t)\nadd_executable(magroom x.cpp)\n' >"$tmp/src/CMakeLists.txt"
printf 'int main(){return 0;}\n' >"$tmp/src/x.cpp"
printf 'int main(){return 0;}\n' >"$tmp/src/mag-room/native/magroom.cpp"
printf 'int main(){return 0;}\n' >"$tmp/src/ops/gitea-ci/bridge.cpp"
printf 'int main(){return 0;}\n' >"$tmp/src/ops/gitea-ci/guard.cpp"
printf 'int main(){return 0;}\n' >"$tmp/src/ops/bots/publishd.cpp"
printf 'int main(){return 0;}\n' >"$tmp/src/mag_deterministic_engine.cpp"
cp "$root/ops/ci/native-build.sh" "$tmp/src/ops/ci/native-build.sh"
cp "$root/ops/build-targets.sh" "$tmp/src/ops/build-targets.sh"
cat >"$tmp/bin/cmake" <<'EOF'
#!/bin/sh
echo "cmake $*" >> "$LOG"
# Real cmake aborts when CMAKE_HOME_DIRECTORY is another worktree.
if [ -f "$MAG_CI_BUILD_DIR/CMakeCache.txt" ]; then
  src=$(sed -n 's/^CMAKE_HOME_DIRECTORY:INTERNAL=//p' "$MAG_CI_BUILD_DIR/CMakeCache.txt" | head -n 1)
  if [ -n "$src" ] && [ "$src" != "$MAG_CI_SRC_ROOT" ]; then
    echo "CMake Error: The source $MAG_CI_SRC_ROOT/CMakeLists.txt does not match $src" >&2
    exit 1
  fi
fi
mkdir -p "$MAG_CI_BUILD_DIR"
case " $* " in
  *" --build "*)
    for t in magroom mag-audit-gateway mag-browser magbot-native mag-bots-publish mag-det mag-numa-swap mag-ci-bridge mag-ci-guard s23ctl \
      test-pho-codec test-guest-pty test-browser-recovery test-jobs-mcp test-pages-native test-browser-events \
      test-guest-description test-guest-curl test-large-stdin test-gitea-account; do
      printf '#!/bin/sh\nexit 0\n' > "$MAG_CI_BUILD_DIR/$t"
      chmod +x "$MAG_CI_BUILD_DIR/$t"
    done
    ;;
esac
exit 0
EOF
chmod +x "$tmp/bin/cmake"
log=$tmp/cmake.log
export LOG=$log MAG_CI_BUILD_DIR=$tmp/src/build MAG_CI_FORCE_BUILD=0 MAG_CI_SRC_ROOT=$tmp/src
PATH="$tmp/bin:$PATH" bash "$tmp/src/ops/ci/native-build.sh" magroom
grep -q 'cmake ' "$log"
[[ -x $tmp/src/build/magroom ]]
[[ -s $tmp/src/build/.mag-native-hash ]]
: >"$log"
PATH="$tmp/bin:$PATH" bash "$tmp/src/ops/ci/native-build.sh" magroom
if grep -q 'cmake ' "$log"; then
  echo 'cache miss on unchanged hash' >&2
  exit 1
fi
printf '\n' >>"$tmp/src/CMakeLists.txt"
PATH="$tmp/bin:$PATH" bash "$tmp/src/ops/ci/native-build.sh" magroom
grep -q 'cmake ' "$log"
# No-arg invocation sources MAG_BUILD_TARGETS (set -u) and still caches.
: >"$log"
PATH="$tmp/bin:$PATH" bash "$tmp/src/ops/ci/native-build.sh"
[[ -x $tmp/src/build/mag-audit-gateway ]]
: >"$log"
PATH="$tmp/bin:$PATH" bash "$tmp/src/ops/ci/native-build.sh"
if grep -q 'cmake ' "$log"; then
  echo 'cache miss after no-arg rebuild' >&2
  exit 1
fi
# Leftover cache from another ~/ci-local/wt/prN-<sha4> must not abort cmake.
printf 'CMAKE_HOME_DIRECTORY:INTERNAL=/old/wt/pr163-72e0\n' >"$tmp/src/build/CMakeCache.txt"
mkdir -p "$tmp/src/build/CMakeFiles"
printf 'stale\n' >"$tmp/src/build/CMakeFiles/stale"
: >"$log"
MAG_CI_FORCE_BUILD=1 PATH="$tmp/bin:$PATH" bash "$tmp/src/ops/ci/native-build.sh" magroom
grep -q 'cmake ' "$log"
[[ ! -e $tmp/src/build/CMakeFiles/stale ]]
echo 'native-build cache PASS'
