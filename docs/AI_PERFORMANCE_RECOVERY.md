# MAG inference recovery / performance (20 October 2026)

## Verified root causes

- MAG is a CPU-only, 2-socket Intel Xeon E5-2680 v4 (14 physical cores per socket)
  with ~252 GiB per NUMA node. Model storage and compute are not the same as
  prompt KV cache. **Q4 weights are already resident/locked in RAM**; prompt
  KV cache uses `--cache-ram 16384`, and the room sends `cache_prompt=true`.
- Host performance measurements from `ops/perf-results/bottleneck/REPORT.md`
  on 16 October measured ik_llama prompt prefill ~43-46 tok/s and text generation
  ~6.4-6.6 tok/s. There is no basis to promise GPU throughput from these CPUs.
- Commit 6eaec8e.. introduced both-socket cutover, later hooked into the
  ordinary deploy: stopping IQ3, restarting Q4 and flushing warm prompt cache.
  Changing NUMA layout without a before/after benchmark was an error.
- The accidental `MAG_ROOM_SINGLE_MODEL=on` default rejected the IQ3 alias
  even while the binary was installed. Reverted to `off` by default.

## Fix delivered

1. Every regular MAG deploy **only** replaces the room services. It must
   never stop or restart either resident inference process.
2. The dual-model UI and per-bot routing are restored by default.
3. The bootstrap baseline is Q4 on node0, IQ3 independently on node1.
   No running host unit is changed by editing that baseline.
4. Read-only host audit: `sudo bash ops/perf/diagnose-ai.sh`.
5. Explicit guarded NUMA model restoration: `sudo bash
   ops/deepseek/restore-dual.sh check`; after hardware review, using a
   pre-existing owner console and with all user work idle:
   `sudo MAG_RECOVERY_ALLOW_RELOAD=YES bash ops/deepseek/restore-dual.sh apply`.
   This will **intentionally reload Q4 once** and may take multiple minutes.
   It stages via `numa-isolation.sh` (which refuses unsafe RAM or unknown
   states), verifies Q4 locality, then starts IQ3 only if the IQ3 SHA, RAM
   and backup preflights pass. No automated deploy invokes this command.
   If staging is refused, inspect and repair the pre-existing NUMA rollback
   state rather than bypassing it.
6. Verify `/health` on both 8090/8091 and prompt-cache hits on successive
   turns. Compare `tokens_cached`, `tokens_evaluated`, first-token latency,
   `prompt_per_second`, `predicted_per_second` and per-node memory pressure.
   Do not call a cold 16 GiB prompt cache a disk I/O fault.

## Bottleneck classification

- Repeated `tokens_cached=0` with an unchanged prefix: cache misses or model
  restarts. Fix routing/prefix/slot cache **first**.
- Cache works, but generation remains ~6 t/s: AVX2 kernel + experts +
  barrier overhead are limiting, not chat strings in Python. Further speed
  requires controlled ik kernel/placement testing or faster accelerators.
- Missing IQ3: service health/NUMA1 headroom, not a missing model file. Never
  start it on node0 simultaneously with Q4.
- High `pgmajfault`/iowait and low `VmLck`: investigate actual disk faults and
  memory locks; avoid `drop_caches` or model eviction in production.

## Release caveat

GitHub CI and source inspection are NOT evidence of live Q4/IQ3 status. Only
host data, the enabled units and two successful direct health checks establish
that recovery actually happened. The owner-only Tailnet console/preflight is
the only supported host path; the GitHub merge cannot restart a private machine.
