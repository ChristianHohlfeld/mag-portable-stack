# DeepSeek V4: NUMA node0 cutover (Ali only)

Base: main c786411. PR does not touch the live host or restart mag-llm. The model, native C++ room,
nginx, audit gateway, GGUF, prompt-cache path and parser are unchanged.

## WHY
Host has 2x E5-2680 v4. Node0 CPU IDs 0-13,28-41; node1 14-27,42-55.
Q4 -rtr --mlock consumes ~147 GiB privately, and mag-model-pin vmtouch
locks another ~144 GiB in the file cache. Both copies cannot fit on 252 GiB node0.
Unpin first; -rtr uses private repacked weights, so GGUF cache can be dropped
after load. The script checks current /proc/PID/maps and rejects mmap weights.
The old custom MAG_NUMA_LOCAL distributed rows to both nodes using MPOL_PREFERRED,
and is removed. numactl --membind=0 uses strict binding (out-of-memory rather than
silently using node1). systemd NUMAPolicy=bind NUMAMask=0 and CPUAffinity reinforce it.

## Before (READ-ONLY)

    sudo bash ops/deepseek/numa-isolation.sh preflight
    sudo bash ops/deepseek/numa-bench.sh before

Preflight validates live -rtr --mlock, not mmap; expected topology and node0
capacity; /health; active cache arguments matched to the newly rendered unit;
no concurrent IQ3; and no unknown ExecStart override. Benchmark records
prefill (pp t/s), generation (tg t/s), TTFT, cache hit/miss,
numastat, process affinity and config; existing http-bench.sh is reused.

## Stage (NEVER restart mag-llm)

    sudo bash ops/deepseek/numa-isolation.sh stage

Backs up /etc/mag-llm/llm.env, original mag-llm base unit and 50-ik.conf
into /var/lib/mag-llm/numa-rollback, then disables/stops mag-model-pin and
blocks future pinning with a persistent systemd ConditionPathExists that is
false. It attempts GGUF-only posix_fadvise(DONTNEED), checks node0 MemFree is at
least 32 GiB; also checks that node0 free RAM plus releasable local Q4\'s anonymous\nweights can fit Q4's measured RssAnon plus 12 GiB of reserve, failing closed\nif NUMA accounting is unavailable. It renders a fresh unit with --cpunodebind=0 --membind=0,
--numa numactl, 16 gen/batch threads and preserved cache flags, removes
the obsolete 50-ik.conf override and daemon-reloads. Does not stop/start
mag-llm and does not alter the main deploy workflow.

Staging is guarded by one backup; no repeated destructive application.
The pin stays disabled through rollback so it cannot OOM the server.

## Manual cutover (Ali, after coordination)

When the gateway has no running inference, ALI (not any automation) deliberately
restarts the model through the existing Restart=always mechanism:

    sudo systemctl kill -s TERM mag-llm.service
    sudo journalctl -fu mag-llm.service

Never use systemctl restart while RefuseManualStop=yes. After /health is back:

    sudo bash ops/deepseek/numa-isolation.sh verify
    sudo bash ops/deepseek/numa-bench.sh after
    sudo bash ops/deepseek/numa-bench.sh compare /var/tmp/BEFORE.md /var/tmp/AFTER.md

In addition, inspect /proc/PID/numa_maps for N1 on model private anonymous
weight pages. CPU affinity alone does NOT guarantee memory locality.
Page cache, shared libraries and other processes may reside elsewhere; claim
only that this model's anonymous weight allocations are node0-only after
verification. Recheck cache reuse, tool-calling and chat after restart.
Chris's reported baseline: 8.1-8.8 generation tok/s; node-local at 16
threads previously observed 8.32 tok/s. Actual result must be measured.
Try 14 vs 16 threads only under a separate measured change if desired.

ROLL BACK if generation throughput degrades, prompt cache breaks, model health
fails, tool calls regress, local RAM pressure spikes or remote weight pages appear:

    sudo bash ops/deepseek/numa-isolation.sh rollback
    # ALI performs second deliberate restart, same command as above.
    sudo bash ops/deepseek/numa-bench.sh rollback

Rollback does not restart anything or re-enable vmtouch. Restoring an
old interleaved process requires a controlled Ali restart.

## PR #31 hot-swap compatibility BLOCKER

PR #31 currently keeps BOTH Q4 147 GiB and IQ3 97 GiB resident, which exceeds
safe node0 capacity once runtime buffers/KV/cache are included. Its IQ3
ExecStart still uses --interleave=all and --numa distribute. This PR adds
an explicit IQ3 ExecStartPre guard that fails immediately, leaving Q4 running
(we do NOT use Conflicts= because that might terminate Q4). PR #31 must be updated before merge to
sequential unload -> wait memory release -> load -> verify -> commit selection;
on failure stop new before reloading old. A no-gap switch isn't possible
with both variants pinned to one 252-GiB node. Prompt-cache persisted slot
files should survive switching; in-memory KV cannot survive model unload.

Secondary different model: mag-llm-secondary.service.in is an uninstalled,
non-enableable template for NUMA node1, loopback port 8092, no GGUF or runtime
deployed. Independent service and optional future per-bot model routing
should be a separate PR.

CI tests are offline only; Ali must perform actual host measurement.
