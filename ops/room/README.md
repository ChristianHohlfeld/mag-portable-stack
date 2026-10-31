# Room: Qwen Uncensored (NUMA0) + GLM-Q2 (NUMA1)

## GLM-Raum: Qwen NUMA0 + GLM-Q2 NUMA1 (31.10.2026)

- **Qwen Uncensored**: slot 1, `qwen-numa0`, NUMA0, `:8191`, IQ4_XS.
- **GLM-Q2**: slot 2, `qwen-numa1`, NUMA1, `:8192`; existing process/PID stays live.
- **GLM-Q1**: stopped and disabled. Its `deepseek-chat` identity and memory/VM remain parked; room history is untouched and IQ1_M stays on disk.
- Deploy stages the existing Qwen GGUF (no download), waits up to 3 hours for the cold-start health check, and verifies the unchanged Q2 PID before/after. A failed cutover rolls back Qwen/Q1 state without stopping or starting Q2.
- Qwen room turns send no tool schemas, force `enable_thinking=false`, `preserve_thinking=false` and `reasoning_effort=none`, cap output at 256 tokens, and suppress partial stream deltas until the final answer passes the hidden-token filter.

The deployed IQ4_XS file is the 98.4 GB quant listed on the [uncensored GGUF card](https://huggingface.co/mradermacher/Qwen3.8-Flash-Next-Uncensored-GGUF), fine-tuned from Qwen's base model. Non-thinking sampling follows the [official Qwen model card](https://huggingface.co/Qwen/Qwen3.8-Flash-Next): temperature 0.7, top_p 0.80, top_k 20, min_p 0, presence_penalty 1.5, repetition_penalty 1.0. The GGUF's embedded Jinja template is used through llama.cpp `--jinja`; Qwen's `enable_thinking=false` and `preserve_thinking=false` controls are sent in `chat_template_kwargs`. llama.cpp documents `reasoning_effort=none` and the `chat_template_kwargs` request field in its [server API](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md); `repeat_penalty` is the llama.cpp request mapping for the card's `repetition_penalty`.

The room-only Qwen unit reuses `/usr/local/lib/mag-qwen/start.sh` and the pinned runtime; it binds CPUs `0-13,28-41`, `NUMAMask=0`, 14 decode threads / 28 batch threads, and port 8191. The standalone NUMA1 Qwen unit remains available for its existing layout and is not started by this GLM layout.

