#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../../.."
bash -n ops/deploy.sh ops/deepseek/restore-dual.sh ops/perf/diagnose-ai.sh
test ! -e ops/deepseek/both-sockets.sh
test ! -e ops/mag-120.d/30-multibot.conf
! grep -Fq 'both-sockets.sh' ops/deploy.sh
! grep -Eq '(warm[.]sh.*restart-llm|systemctl (stop|restart|kill).*mag-llm|systemctl disable --now mag-llm-iq3)' ops/deploy.sh
# Historical mag-120.service keeps its fallback default, but the active Ornith
# drop-in MUST override it: four bots, not one DeepSeek bot.
grep -Fq 'Environment=MAG_ROOM_SINGLE_BOT=auto' ops/mag-120.service
grep -Fxq 'Environment=MAG_ROOM_SINGLE_BOT=' ops/mag-120.d/20-room-audit.conf
grep -Fxq 'EnvironmentFile=-/etc/mag-stack/room-model.env' ops/mag-120.d/20-room-audit.conf
grep -Fxq 'EnvironmentFile=-/etc/mag-stack/room-model.env' ops/mag-120.service
grep -Fxq 'MAG_ROOM_SINGLE_BOT=' mag-room/.env.example
grep -Fxq 'MAG_ROOM_MODEL_ID=deepseek-v4-flash-0731' mag-room/.env.example
grep -Fxq 'Environment=MAG_ROOM_DUAL_NUMA=1' ops/mag-120.d/30-dual-numa.conf
grep -Fq 'SINGLE_BOT.clear();' mag-room/native/magroom.cpp
grep -Fq 'MAG_SECONDARY_MODEL=ornith' ops/ornith/cutover.sh
grep -Fq 'mag-llm-ornith-node@0.service mag-llm-ornith-node@1.service' ops/ornith/cutover.sh
grep -Fq 'MAG_SECONDARY_MODEL=qwen' ops/ornith/rollback.sh
grep -Fq 'cache_prompt' mag-room/native/magroom.cpp
# Legacy DeepSeek tuning remains archived as data; deploy must not apply it.
grep -Fq -- '--cpunodebind=0 --membind=0' ops/deepseek/llm.env.mag
grep -Fq 'IK_SLOT_SAVE=1' ops/deepseek/llm.env.mag
! grep -Fq 'ops/deepseek/llm.env.mag' ops/deploy.sh
grep -Fq 'rm -f /etc/systemd/system/mag-120.service.d/30-multibot.conf' ops/deploy.sh
echo 'PASS two DeepSeek NUMA bots, Qwen-only rollback, archived legacy tuning not activated'
