#!/bin/bash
# Back to mainline llama.cpp (llama serve b11630) for mag-llm. Model stays pinned by mag-model-pin.
set -e
rm -f /etc/systemd/system/mag-llm.service.d/50-ik.conf
systemctl daemon-reload
systemctl kill -s TERM mag-llm.service   # Restart=always brings it back with the original ExecStart
echo "rolled back; watch: journalctl -fu mag-llm"
