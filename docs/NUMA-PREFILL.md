# MAG CPU-only NUMA prompt latency

Hardware target: dual Xeon E5-2680 v4, 512 GB DDR4, two resident models:
Q4 (NUMA0, :8090), Qwen IQ4_XS (NUMA1, :8091). One model process per NUMA
island. Model inference is **not** inside the bots' two KVM VMs.

## What changed
- Qwen initial room transcript: 6,000 bytes / at most 14 recent messages by
  default; Q4 retains its 16,000-byte / 30-message configuration. Full room,
  user files, memories and audit traces remain on disk.
- Per-bot history windows: Qwen's short window cannot invalidate Q4's
  cached prefix. Tool names, JSON input schemas and authorization are unchanged.
  Qwen shortens verbose descriptions inside tool declarations (default on).
- Qwen completion maximum defaults to 4,096 tokens, with headroom for its
  16,384-token server context. Large output can be split into multiple tool
  calls. Q4 retains MAG_ROOM_MAX_TOKENS (normally 8,192).
- Qwen tool response snippets default to 3,000 bytes; the complete result
  remains in the user-visible trace and can be requested again.
- Last prompt cache/prefill metrics reported separately for Q4/Qwen in
  authenticated owner's /api/state.model_perf (timings only, no content).
- No model binaries, model weights, placement, running services or KVM
  snapshots are modified by this performance patch itself.

## Measurement (read-only; no prompt cache eviction)
```bash
sudo bash /home/chris/mag-stack/ops/perf/diagnose-ai.sh
sudo journalctl -u mag-120.service --since '-3 hours' --no-pager -o cat |
  python3 /home/chris/mag-stack/ops/perf/context-report.py
```

Repeated `tokens_cached=0` on *similar subsequent turns* indicates cache
invalidation. Compare medians by model, never infer a throughput gain from
this code alone: after merge the host must serve real chat/tool turns and the
measured `prompt_tps` and cache hit ratio should be compared with baseline.
After host reboot the models must reload weights and first prompt prefixes;
RAM retention across power loss is impossible.

## Knobs, override through an operator-reviewed systemd drop-in
- `MAG_ROOM_QWEN_TRANSCRIPT_BYTES` default 6000 (3000–16000)
- `MAG_ROOM_QWEN_MAX_HISTORY` default 14 (4–80)
- `MAG_ROOM_QWEN_CONTEXT_BYTES` default 42000 (24000–100000)
- `MAG_ROOM_QWEN_TOOL_CONTEXT_BYTES` default 3000 (1000–12000)
- `MAG_ROOM_QWEN_MAX_TOKENS` default 4096 (512–8192)
- `MAG_ROOM_QWEN_COMPACT_TOOLS` default 1; 0 keeps full original descriptions

Caution: raising caps may make cold prompt evaluation take minutes on the
CPU-only 98 GB Qwen. Changing any of these only restarts the room process,
not the resident model services. Never change NUMA placement or Qwen llama
thread/batch settings on live services without host benchmark/rollback.
