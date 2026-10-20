# Qwen3.8 — start after download

After the single `hf download` command has **finished successfully**, run
this on the MAG host in its checked-out repository:

```bash
cd ~/mag-stack
sudo bash ops/qwen/go-live.sh
```

One command: validate that MAG's latest `green` release has deployed; use
the independent pinned qwen4exp llama.cpp runtime (build it if missing);
pin the upstream GGUF SHA256; validate the complete downloaded weights;
stage Qwen on NUMA1; wait for room/jobs/backups to be idle; replace IQ3 on
NUMA1; test model chat, automatic structured tool_calls, a real tool-result
continuation, reasoning_content, and streaming; then validate the same
model through MAG's audit gateway. On a failed cutover, restore IQ3/gateway.

**Q4 on NUMA0 is never stopped or restarted.** The existing coding bot,
memory, VM, browser, Git, permissions, and MAG MCP tool dispatcher remain
unchanged. Models cannot both occupy NUMA1 or port 8091.

Then open MAG: the top model buttons use the live room model catalog. Choose
**1 · Qwen** (instead of **0 · Q4**). Sending a normal message, toggling
Thinking and running the bot's existing tools use the selected Qwen
backend. Model selection itself does not restart the LLM processes.

If the script refuses to start, **read its actual error**. In particular,
the GGUF may still be downloading or the automated MAG deployment may not
yet have promoted its current checkout. Do not bypass preflights or start
two large NUMA1 models together.

```bash
sudo bash ops/qwen/cutover.sh status
sudo bash ops/qwen/cutover.sh rollback
```

Only run rollback when chat/jobs/backups are idle. The browser's initial
static IQ3 labels are replaced from `/api/state.models` when it connects;
the sidebar's legacy caption may still say Q4/IQ3 until its UI is revised.
This script verifies model protocol and routing, but it does not constitute
a live end-to-end test of every external MCP provider.
