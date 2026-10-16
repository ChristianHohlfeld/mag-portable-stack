# DeepSeek-V4-Flash on mag – bottleneck analysis and fix (2026-10-16)

## Result
| | before (llama.cpp b11630, `llama serve`) | after (ik_llama.cpp, live since 10:41 Berlin) |
|---|---|---|
| Generation, live via gateway | 3.26-3.75 t/s | **6.35-6.55 t/s** (+75 %) |
| Prompt, live via gateway (850-6600 tok) | 19-22 t/s | **42.6-46.5 t/s** (2x) |
| llama-bench tg32 | 3.63 (t16) / 3.08 (t28) | 6.50 (t28) |
| llama-bench pp512 / pp2048 | 20.2-20.4 (ub 256-2048) | 42.5 / 44.3 (ub 2048) |
| Anon RAM (repack copy) | 138-145 GiB | 2 GiB (no -rtr; mmap from pinned page cache + mlock) |
| VmLck | 142 GiB | 144 GiB |

## Bottleneck (evidence)
perf on the live server (stripped binary, addresses disassembled) + the same-version debug build (RelWithDebInfo, b11630 6868a52cb):
- 45 % `ggml_vec_dot_q8_0_q8_0`: the dense Q8_0 tensors (attn_q_b, attn_output_a/b, shared experts, about 6 GiB/token) have **no x86 CPU_REPACK** in mainline (repack.cpp only has a Q8_0 path for NEON/RVV), so they fall back to a single-accumulator AVX2 vec_dot per row.
- 25 % `ggml_vec_dot_mxfp4_q8_0`. Experts are MXFP4 (137 GiB). The AVX2 repack `mxfp4_8x8_q8_0` applies and is the 138 GiB anon copy.
- About 17-20 % OpenMP/ggml barrier spin (pause loops at 0x20154c0 / 0x20c7c40 in the stripped binary), plus small ops (hc_post, concat, FA).
- Scaling: t4 1.86, t8 3.05, t12 2.93, t14 (node0) 3.56, t16 3.63, t28 3.08, t56 2.56 t/s. Throughput saturates at about 34 GB/s effective (9.3 GB of weights per token). That is far below the about 120 GB/s the 2 sockets can deliver, and more threads make it slower, so the limit is sync/kernel efficiency, not DRAM. This matches the BIOS 2400 result (+1-3 %).
- Flags in mainline: --poll 0/100 no effect (3.57/3.58), fa 0 is worse (3.31), --repack 0 costs only 3 % (3.63 vs 3.75).
- One NUMA node (cpunodebind 0, t14) 3.56, the same as both sockets. A node0-only test with membind and -mmp 0 or -rtr is not possible without dropping the live model (node0 had only 107 GiB free and needs 140).
- Theoretical: about 3.2 GiB of experts (6 of 256, MXFP4) + about 6 GiB of dense Q8_0 per token, about 9.3 GB in total. At about 100 GB/s practical, that gives about 10 t/s as the ceiling. ik reaches 6.5.

## ik_llama.cpp (commit in ops/deepseek/ik/COMMIT, 8d21687c)
- interleave t16: pp512 28.2, tg 5.91; t28: tg 6.50; node0 t14: 4.54; -rtr t16: pp 33.7 but tg 5.59 (worse, and +138 GiB anon). The winner is t28, no -rtr, fmoe default on, fa on, b/ub 2048.
- API check: /props model_path ok, streaming ok, reasoning_content ok, enable_thinking=false ok, abort cancels the task, prompt cache ok (607 -> 5 tokens). preflight-tests.py --quick through the gateway: all green (tool calls, streaming, audit log).

## Live change
- `/etc/systemd/system/mag-llm.service.d/50-ik.conf` -> `/home/chris/.local/lib/ik-llama/ikrun.sh 8090 --mlock` (binary copied to ~/.local/lib/ik-llama/).
- Rollback: `sudo bash /home/chris/.local/lib/ik-llama/rollback.sh` (removes the drop-in, daemon-reload, TERM, and Restart=always brings back the old llama serve).
- The room (mag-120) was stopped from about 10:22 to 10:47 Berlin for the benches (Chris approved). It is up again, :8446 returns 200, and model_info shows engine ik_llama.cpp.

## Not done / open for Claude
- Requant of the dense Q8_0 tensors to Q6_K: the dry run is ok (510 tensors, 147.9 -> 146.5 GiB), but it was aborted during the benches. The gain is at most about 15 % of the bytes per token. Not pursued after ik; no perplexity run.
- An AVX2 Q8_0 repack/vec_dot port in mainline is no longer needed for the live server (ik has its own iqk kernels).
- No -ctk q8_0 (#25382). Watch the prompt-cache context loss bug (#25259) in ik as well.
- The http-bench.sh standard run was aborted by the room becoming busy. The live numbers above come from its runs in the journal (pp 852/3332/6641).
Links: https://github.com/ggml-org/llama.cpp/pull/24162 (HC ops, nodes 30k->9k, TG x2; already in b11630), https://github.com/ikawrakow/ik_llama.cpp/issues/1752 (V4-Flash CPU 9.7 t/s, compute-bound), ik V4 support via ik #2165.
