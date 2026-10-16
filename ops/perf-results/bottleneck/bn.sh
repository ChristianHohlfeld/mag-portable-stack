#!/bin/bash
# usage: bn.sh NAME "numactl-prefix" "llama-bench args" [binary]
set -u
NAME=$1; PRE=$2; ARGS=$3; BIN=${4:-$HOME/bneck/llama.cpp/build-dbg/bin/llama-bench}
M=/home/chris/models/deepseek-v4/DeepSeek-V4-Flash-0731-UD-Q4_K_XL-00001-of-00005.gguf
OUT=$HOME/bneck/res; mkdir -p $OUT
# idle check: no request in last 120s and slot idle
if journalctl -u mag-llm --since "-60s" --no-pager | grep -qE "launch_slot|print_timing"; then echo "BUSY: room active, skip $NAME"; exit 3; fi
avail=$(awk '/MemAvailable/{print int($2/1048576)}' /proc/meminfo)
need=5; echo "$ARGS" | grep -q -- "--repack 0" || need=145
if [ $((avail-need)) -lt 40 ]; then echo "RAM: avail ${avail}G need ${need}G -> skip $NAME"; exit 4; fi
echo "== $NAME  avail=${avail}G  $(date -u +%T)Z" | tee $OUT/$NAME.txt
$PRE $BIN -m $M $ARGS -o md >> $OUT/$NAME.txt 2>$OUT/$NAME.err &
BP=$!
# watchdog
( while kill -0 $BP 2>/dev/null; do a=$(awk '/MemAvailable/{print int($2/1048576)}' /proc/meminfo); if [ $a -lt 40 ]; then echo "WATCHDOG kill avail=${a}G" >> $OUT/$NAME.txt; kill -9 $BP; fi; sleep 1; done ) &
# wait until generation phase-ish then sample counters
sleep ${WARM:-25}
if kill -0 $BP 2>/dev/null; then
 sudo perf stat -a -e uncore_imc/cas_count_read/,uncore_imc/cas_count_write/,uncore_qpi_0/txl_flits_g0.data/,uncore_qpi_1/txl_flits_g0.data/,cycles,instructions,context-switches -- sleep 8 >> $OUT/$NAME.perf 2>&1
 mpstat -N ALL 2 2 2>/dev/null | tail -4 >> $OUT/$NAME.perf
fi
wait $BP; echo "exit=$?" >> $OUT/$NAME.txt
cat $OUT/$NAME.txt; grep -E "MiB|GiB|seconds|Avg" $OUT/$NAME.perf | head; grep -iE "repack|buffer size" $OUT/$NAME.err | head -5
