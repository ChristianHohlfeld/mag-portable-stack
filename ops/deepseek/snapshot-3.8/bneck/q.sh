#!/bin/bash
cd ~/bneck
IK=$HOME/bneck/ik_llama.cpp/build/bin/llama-bench
waitidle(){ while journalctl -u mag-llm --since "-60s" --no-pager | grep -qE "launch_slot|print_timing"; do sleep 20; done; }
run(){ waitidle; WARM=25 ./bn.sh "$@" | grep -E "tg|pp|BUSY|RAM|WATCHDOG|error" ; }
run ik_il_t16 "numactl --interleave=all" "-p 512 -n 32 -r 2 -t 16 -fa 1 -fmoe 1 -mmp 1 --numa distribute" $IK
run ik_node0_t14 "numactl --cpunodebind=0 --membind=0" "-p 0 -n 32 -r 2 -t 14 -fa 1 -fmoe 1 -mmp 1 --numa numactl" $IK
run ik_il_t28 "numactl --interleave=all" "-p 0 -n 32 -r 2 -t 28 -fa 1 -fmoe 1 -mmp 1 --numa distribute" $IK
run ik_il_t16_rtr "numactl --interleave=all" "-p 512 -n 32 -r 2 -t 16 -fa 1 -fmoe 1 -rtr 1 --numa distribute" $IK
run ml_pp_ub "numactl --interleave=all" "-p 512 -n 0 -r 1 -t 28 -b 2048 -ub 256,512,1024,2048 --repack 1 -lm mmap -fa 1 --numa distribute"
echo QUEUE_DONE
