# Room: Qwen Uncensored (NUMA0) + GLM-Q2 (NUMA1)

## GLM-Raum: Qwen NUMA0 + GLM-Q2 NUMA1 (31.10.2026)

- **Qwen Uncensored**: slot 1, `qwen-numa0`, NUMA0, `:8191`, IQ4_XS.
- **GLM-Q2**: slot 2, `qwen-numa1`, NUMA1, `:8192`; existing process/PID stays live.
- **GLM-Q1**: stopped and disabled. Its `deepseek-chat` identity, memory/VM and room history remain parked; IQ1_M stays on disk.
- Deploy stages the existing Qwen GGUF (no download), waits up to 3 hours for the cold-start health check, and verifies the unchanged Q2 PID before/after. A failed cutover rolls back Qwen/Q1 state without stopping or starting Q2.
- Qwen room turns send no tool schemas, force `enable_thinking=false`, cap output at 256 tokens, and suppress partial stream deltas until the final answer passes the hidden-token filter.

Qwen non-thinking sampling follows the [official Qwen3.8-Flash-Next model card](https://huggingface.co/Qwen/Qwen3.8-Flash-Next): temperature 0.7, top_p 0.80, top_k 20, min_p 0, presence_penalty 1.5, repetition_penalty 1.0. The GGUF's embedded Jinja template is used through llama.cpp `--jinja`; `enable_thinking=false` is passed in `chat_template_kwargs` (see [llama.cpp server docs](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)). llama.cpp's `repeat_penalty` request field is the mapping for the card's `repetition_penalty`.

The room-only Qwen unit reuses `/usr/local/lib/mag-qwen/start.sh` and the pinned runtime; it binds CPUs `0-13,28-41`, `NUMAMask=0`, 14 decode threads / 28 batch threads, and port 8191. The standalone NUMA1 Qwen unit remains available for its existing layout and is not started by this GLM layout.

