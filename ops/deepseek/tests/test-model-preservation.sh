#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../../.."
bash -n ops/deploy.sh ops/deepseek/restore-dual.sh ops/perf/diagnose-ai.sh
test ! -e ops/deepseek/both-sockets.sh
test ! -e ops/mag-120.d/30-multibot.conf
! grep -Fq 'both-sockets.sh' ops/deploy.sh
! grep -Eq '(warm[.]sh.*restart-llm|systemctl (stop|restart|kill).*mag-llm|systemctl disable --now mag-llm-iq3)' ops/deploy.sh
grep -Fq 'MAG_ROOM_SINGLE_BOT=auto' ops/mag-120.service
grep -Fq 'MAG_ROOM_SINGLE_BOT=auto' ops/mag-120.d/20-deepseek-audit.conf
grep -Fq 'MAG_ROOM_SINGLE_BOT=auto' mag-room/.env.example
grep -Fq -- '--cpunodebind=0 --membind=0' ops/deepseek/llm.env.mag
grep -Fq 'THREADS=16' ops/deepseek/llm.env.mag
grep -Fq 'IK_SLOT_SAVE=1' ops/deepseek/llm.env.mag
grep -Fq -- '--cache-ram 16384' ops/mag-llm.service
grep -Fq 'cache_prompt' mag-room/native/magroom.cpp
grep -Fq 'MAG_RECOVERY_ALLOW_RELOAD' ops/deepseek/restore-dual.sh
grep -Fq 'numa-isolation.sh" verify' ops/deepseek/restore-dual.sh
grep -Fq 'health 8091' ops/deepseek/restore-dual.sh
grep -Fq 'rm -f /etc/systemd/system/mag-120.service.d/30-multibot.conf' ops/deploy.sh
grep -Fq 'if (!SINGLE_BOT.empty()) return active_room_model();' mag-room/native/magroom.cpp
echo 'PASS single active bot, warm NUMA Q4/IQ3 switching and no compatibility entrypoint'
