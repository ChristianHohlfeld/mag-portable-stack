#!/bin/bash
# ik_llama.cpp server for DeepSeek-V4-Flash on mag. Usage: ikrun.sh PORT [extra args]
P=${1:-8090}; shift
exec /usr/bin/numactl --interleave=all /home/chris/.local/lib/ik-llama/llama-server \
  --model /home/chris/models/deepseek-v4/DeepSeek-V4-Flash-0731-UD-Q4_K_XL-00001-of-00005.gguf \
  --alias deepseek-v4-flash-0731 --host 127.0.0.1 --port "$P" \
  --ctx-size 32768 --parallel 1 -t 28 -tb 28 --numa distribute -b 2048 -ub 2048 -fa on \
  --cache-ram 16384 --jinja \
  --chat-template-file /home/chris/llama-templates/deepseek-ai-DeepSeek-V4-Flash-0731.jinja \
  --reasoning-format deepseek --temp 1.0 --top-p 0.95 --min-p 0.01 --metrics "$@"
