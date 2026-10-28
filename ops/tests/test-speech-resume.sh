#!/usr/bin/env bash
# mag-test-timeout: 60
if [ -z "${MAG_TEST_DEADLINE_ARMED:-}" ]; then
  export MAG_TEST_DEADLINE_ARMED=1
  exec timeout --kill-after=5s 60s bash "$0" "$@"
fi
# Deterministic offline regression for interrupted CDN model downloads.
set -euo pipefail
root=$(cd -- "$(dirname -- "$0")/../.." && pwd)
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
# Exercise the actual function shipped to production, not a copied variant.
lib=$(sed -n '/^download_model() {/,/^}/p' "$root/ops/vendor-speech-assets.sh")
[[ "$lib" == *'--continue-at -'* ]] || { echo 'resume implementation missing' >&2; exit 1; }
mkdir -p "$tmp/bin"
cat > "$tmp/bin/curl" <<'MOCK'
#!/usr/bin/env bash
set -euo pipefail
dest=''
while (( $# )); do
  case "$1" in
    --output) dest=$2; shift 2 ;;
    *) shift ;;
  esac
done
[[ -n "$dest" ]] || exit 91
n=$(cat "$COUNT_FILE" 2>/dev/null || echo 0)
n=$((n + 1))
printf '%s' "$n" > "$COUNT_FILE"
case "$TEST_CASE" in
  interrupted)
    case "$n" in
      1) printf 'abc' >> "$dest"; exit 18 ;;
      2) printf 'def' >> "$dest"; exit 28 ;;
      *) printf 'ghij' >> "$dest"; exit 0 ;;
    esac ;;
  unsupported) printf 'abc' >> "$dest"; exit 33 ;;
  stagnant) exit 28 ;;
  oversized) printf 'abcdefghijk' >> "$dest"; exit 0 ;;
  *) exit 92 ;;
esac
MOCK
chmod +x "$tmp/bin/curl"
# Overrides the time-only backoff, not the production algorithm.
runner="$lib"$'\nsleep() { :; }\ndownload_model "$1" "$2" "$3"'
export COUNT_FILE="$tmp/count"
export PATH="$tmp/bin:$PATH"
export TEST_CASE=interrupted
bash -euo pipefail -c "$runner" bash https://example.invalid/model "$tmp/model" 10
[[ $(cat "$tmp/model") == abcdefghij ]]
[[ $(cat "$COUNT_FILE") == 3 ]]
for case in unsupported stagnant oversized; do
  export TEST_CASE="$case"
  : > "$COUNT_FILE"
  if bash -euo pipefail -c "$runner" bash https://example.invalid/model "$tmp/model" 10; then
    echo "unsafe model download unexpectedly accepted: $case" >&2
    exit 1
  fi
done
echo 'Moonshine resumable download PASS'
