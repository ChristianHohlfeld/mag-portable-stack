#!/bin/bash
# kernel-dev bench helper. No drop_caches. mmap only. Watchdog kills bench if MemAvailable < 40 GiB.
cd ~/bneck/kernel-dev; mkdir -p results
MOD=$HOME/models/deepseek-v4/DeepSeek-V4-Flash-0731-UD-Q4_K_XL-00001-of-00005.gguf
RES=results/results.tsv; [ -f $RES ] || printf "build\tthreads\ttest\tavg_ts\tstddev\n" > $RES
wd() { while kill -0 $1 2>/dev/null; do a=$(awk '/MemAvailable/{print int($2/1048576)}' /proc/meminfo); [ $a -lt 40 ] && { echo "WATCHDOG kill avail=${a}G" >> results/watchdog.log; pkill -f kernel-dev/.*/llama-bench; pkill -f kernel-dev/.*/test-backend-ops; }; sleep 2; done; }
bench() { # $1 tag $2 bin $3 threads [extra]
  tag=$1 bin=$2 t=$3; shift 3
  OMP_WAIT_POLICY=active numactl --interleave=all $bin -m $MOD -mmp 1 -t $t -fa 1 -p 512 -n 128 -r 3 -o json "$@" > results/ab_${tag}_t$t.json 2> results/ab_${tag}_t$t.log &
  p=$!; wd $p & wait $p
  python3 -c "
import json,sys
for x in json.load(open('results/ab_${tag}_t$t.json')):
  n='pp512' if x['n_gen']==0 else 'tg128'
  print('$tag\t$t\t'+n+'\t%.2f\t%.2f'%(x['avg_ts'],x['stddev_ts']))" >> $RES
}
