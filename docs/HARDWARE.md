# MAG host and model hardware

The reported MAG host configuration is **two Intel Xeon E5-2680 v4 processors**, 28 physical CPU cores / 56 threads total, Broadwell AVX2/FMA/F16C, no AVX-512/VNNI/AMX, **512 GiB DDR4-2400** over two NUMA nodes.

The local model is DeepSeek-V4-Flash-0731 UD-Q4_K_XL (~145 GB GGUF; 284B total / 13B active MoE parameters), served by **ik_llama.cpp** with `--jinja`. Previously reported settings include `-rtr`, 16 generation threads, 28 batch threads, `-ub 1024`, F16 KV and a 32768-token context. Previous reported throughput: roughly 7.9 output tokens/s and 35–50 prefill tokens/s. These are operator observations, not release acceptance thresholds.

One warmed model instance stays in RAM while mag-room/native and nginx are deployed. **Do not stop, restart, reload, or rebuild in place `mag-llm` during this release**. Use an independent `ik_llama.cpp` build path and retain a known-good binary if CPU BLAS performance is reviewed later. GPU/S23/VM hardware measurements must not be inferred from these CPU figures.
