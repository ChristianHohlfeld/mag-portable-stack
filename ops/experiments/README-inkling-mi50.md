# Inkling 2 Mini – optional MI50 GPU experiment

This is **not** a third MAG bot, a service or an automatic deployment. It is a
single, opt-in experiment on the **one AMD Instinct MI50 (gfx906)**, which belongs
to MAG Idol. Lena and Noah remain on their production CPU-only Gemma 4 runtime.
No file in ops/models/, any systemd unit, power cap, model slot, release or
production model is modified.

Upstream: https://huggingface.co/unsloth/Inkling-2-Mini-GGUF

The upstream root GGUFs use a different tensor layout. The test deliberately
downloads the **Mainline_Support** GGUFs and renames .gguf_file to .gguf, using
a fresh upstream llama.cpp source checkout, not MAG's pinned CPU runtime. The
required loop-scale fix was merged to llama.cpp master as
https://github.com/ggml-org/llama.cpp/pull/32704 on 2026-11-21.
A build on MI50/ROCm 6.3.4 is **not guaranteed**: real compatibility and
tokens/sec are results to measure, not claims.

Run as MAG's configured owner on the MAG host, **not as root**:

    python3 ops/experiments/inkling-mi50.py preflight
    python3 ops/experiments/inkling-mi50.py prepare
    python3 ops/experiments/inkling-mi50.py download
    python3 ops/experiments/inkling-mi50.py run

Requirements: git, cmake, ninja, locally unpacked ROCm 6.3.4 from
ops/video/h3/rocm.sh, Hugging Face's hf CLI, disk space for model/cache.
The build goes into ~/.local/inkling2-mi50-test and the model goes into
~/models/inkling2-mi50-test; neither is auto-deployed. The source SHA is
recorded in ~/.local/inkling2-mi50-test/revision.txt.

VRAM selection uses the **actual sysfs value**:
- 16 GiB MI50: Mainline_Support/UD-Q3_K_XL (about 13.63 GB)
- 32 GiB MI50: Mainline_Support/UD-Q4_K_M (about 16.61 GB)

A lower capacity, absent sensors, unavailable power cap, insufficient **free**
VRAM or a busy/queued Idol job prevents a GPU benchmark. Before GPU use the
test takes /var/lib/mag-idol/gpu.lock **non-blocking** and refuses to run if
already locked. During its two bounded commands, it checks temperature and
power cap every second and terminates its own process group above 80 C, if
sensors disappear, if the power cap exceeds 78 W or on timeout. It never
raises a power limit. It refuses to report GPU success unless llama.cpp
explicitly logs full offloading. A model may still fail from a HIP kernel
limitation or OOM; this is experimental.

The MI50's persistent 3D/image/video services may reserve VRAM even when
idle. Do **not** stop those services to force the benchmark to fit. The
test may also briefly delay a newly enqueued Idol job while it owns the
shared GPU lock. Do not run it during interactive media work.

Outputs: ~/.local/inkling2-mi50-test/inkling-sample-*.log (coding answer and
model offload logs) and inkling-perf-*.json (llama-bench throughput). Do not
infer useful coding-agent quality from a single prompt: only after the GPU
runtime is demonstrated should a repeatable suite compare model accuracy,
tool calling and wall-clock latency against the CPU bots.

To undo the experiment, no system rollback is necessary. The tool never
removes the experiment's weights or build automatically.
