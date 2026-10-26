#!/usr/bin/env bash
# Run once when the Hugging Face download has finished.
# Only the Qwen side of NUMA1 is changed; Q4 on NUMA0 remains resident.
set -euo pipefail
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
GGUF=/home/chris/models/qwen3.8-flash-next-uncensored/Qwen3.8-Flash-Next-Uncensored.IQ4_XS.gguf
fail(){ echo "Qwen: $*" >&2; exit 4; }
[[ $EUID == 0 ]] || fail "run with sudo"
[[ $(git -c safe.directory="$ROOT" -C "$ROOT" symbolic-ref --short HEAD) == main ]] || fail "checkout must be main"
[[ -z $(git -c safe.directory="$ROOT" -C "$ROOT" status --porcelain --untracked-files=no) ]] || fail "checkout has local edits"
[[ $(cat /opt/mag-stack/current/SOURCE_COMMIT 2>/dev/null || true) == "$(git -c safe.directory="$ROOT" -C "$ROOT" rev-parse HEAD)" ]] || fail "MAG auto-deploy is not finished; check mag-auto-update.service"
[[ -f "$GGUF" && ! -L "$GGUF" ]] || fail "download GGUF missing"
[[ $(stat -c %s "$GGUF") -ge 90000000000 ]] || fail "download incomplete"
REV=$(cat "$ROOT/ops/qwen/runtime.sha")
BIN=/home/chris/.local/qwen4exp/$REV/bin/llama-server
if [[ $(cat /etc/mag-stack/secondary-model.env 2>/dev/null || true) == MAG_SECONDARY_MODEL=qwen ]] && systemctl is-active --quiet mag-llm-qwen.service; then
  python3 -I "$ROOT/ops/qwen/smoke.py" --port 8080 --quick
  echo "Qwen already live. Open MAG and choose 1 · Qwen."
  exit 0
fi
if [[ ! -x "$BIN" || $(cat "$(dirname "$BIN")/source-commit" 2>/dev/null || true) != "$REV" ]]; then
  echo "Building pinned Qwen llama.cpp ..."
  runuser -u chris -- env HOME=/home/chris nice -n 19 ionice -c3 bash "$ROOT/ops/qwen/build-runtime.sh"
fi
PIN=/home/chris/models/qwen3.8-flash-next-uncensored/upstream.sha256
if [[ ! -s "$PIN" ]]; then
  echo "Fetching independent upstream SHA256 ..."
  runuser -u chris -- env HOME=/home/chris python3 "$ROOT/ops/qwen/pin-model.py"
fi
echo "Verifying complete GGUF and staging Qwen on NUMA1 ..."
nice -n 19 ionice -c3 bash "$ROOT/ops/qwen/install.sh" stage
rm -f /etc/mag-qwen/runtime.env
bash "$ROOT/ops/qwen/cutover.sh" check
echo "Activating Qwen; live chat, reasoning and structured tool calls must pass ..."
bash "$ROOT/ops/qwen/cutover.sh" apply
echo "Ready: MAG frontend model selector offers 0 · Q4 and 1 · Qwen."
echo "Select 1 · Qwen, send a message, and use Thinking / MCP tools normally."

