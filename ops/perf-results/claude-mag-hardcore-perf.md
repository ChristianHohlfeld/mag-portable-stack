# Claude: Hardcore-Performance-Plan mag (2026-10-16 ~10:00)
Chat: https://claude.ai/chat/996029f7-f120-4761-9efa-657c32177fe2
Claude hat keinen mag-Zugriff; Flags gegen README/Binary prüfen.

Diagnose: Compute-bound, nicht RAM (43 % CPU, 4–19 GB/s, Threads wirkungslos). Ursachen: Cross-Socket QPI, Spin/Barrier, Q4_K-Dequant auf AVX2. Hebel: Single-Socket, ik_llama.cpp fused MoE + Repack, exaktes Pinning.

## 0) Vorab
```bash
sudo dmidecode -t memory | grep -E "Configured Memory Speed" | sort | uniq -c
numactl -H; lscpu | grep -E "NUMA|Thread|MHz"
numastat -p $(pgrep -f llama-server)
sudo ./mlc --max_bandwidth -k0-13   # Erwartung ~55–65 GB/s pro Socket
```

## 1) Kernel-Basis (0–15 %)
```bash
sudo cpupower frequency-set -g performance
sudo cpupower idle-set -D 0                       # dauerhaft: intel_idle.max_cstate=1
echo 0 | sudo tee /proc/sys/kernel/numa_balancing
echo always | sudo tee /sys/kernel/mm/transparent_hugepage/enabled
echo 3 | sudo tee /proc/sys/vm/drop_caches        # vor JEDEM Placement-Wechsel
```

## 2) Testmatrix (M=/pfad/DeepSeek-V4-Flash-UD-Q4_K_XL.gguf)
T0 Baseline:
```bash
./llama-bench -m $M -p 512 -n 128 -t 28 -r 3 -o md
```
T1 Single-Socket (höchste Prio, Erwartung 4,5–6 t/s):
```bash
echo 3 | sudo tee /proc/sys/vm/drop_caches
numactl --cpunodebind=0 --membind=0 ./llama-bench -m $M -mmp 0 --numa numactl \
  -t 10,12,13,14 -p 512 -n 128 -r 3 -o md
```
T2 Interleave beide Sockets:
```bash
echo 3 | sudo tee /proc/sys/vm/drop_caches
numactl --interleave=all ./llama-bench -m $M -mmp 0 --numa numactl -t 26,28 -p 512 -n 128 -r 3 -o md
```
T3 --numa distribute (mit mmap):
```bash
echo 3 | sudo tee /proc/sys/vm/drop_caches
./llama-bench -m $M --numa distribute -t 28 -p 512 -n 128 -r 3 -o md
```
T4 Server-Threads getrennt:
```bash
numactl -N0 -m0 ./llama-server -m $M --no-mmap --numa numactl -t 13 -tb 14 ...
```
T5 Prompt-Batch (Erwartung Prompt 22 → 30–45):
```bash
numactl -N0 -m0 ./llama-bench -m $M -mmp 0 -t 14 -p 2048 -n 0 -b 2048 -ub 256,512,1024,2048 -o md
```
T6 Flash-Attn/KV (Achtung: q8_0 K-Cache Bug #25382 bei V4):
```bash
numactl -N0 -m0 ./llama-bench -m $M -mmp 0 -t 13 -fa 0,1 -ctk f16,q8_0 -p 512 -n 128 -d 0,8192 -o md
```
T7 Neuester Master:
```bash
git -C llama.cpp pull && cmake -S llama.cpp -B build -DGGML_NATIVE=ON -DGGML_CPU_REPACK=ON \
  -DGGML_OPENMP=ON -DCMAKE_BUILD_TYPE=Release && cmake --build build -j28
```
T8 ik_llama.cpp (Erwartung Gen +30–60 %, Prompt x1,5–2; erst prüfen ob V4-Arch lädt):
```bash
git clone https://github.com/ikawrakow/ik_llama.cpp && cd ik_llama.cpp
cmake -B build -DGGML_NATIVE=ON -DGGML_AVX2=ON -DGGML_IQK_FA_ALL_QUANTS=ON -DCMAKE_BUILD_TYPE=Release
cmake --build build -j28
echo 3 | sudo tee /proc/sys/vm/drop_caches
numactl -N0 -m0 ./build/bin/llama-bench -m $M -mmp 0 -t 13 -fa 1 -fmoe 1 -rtr 1 -ub 512 -p 512 -n 128 -r 3 -o md
# optional offline: ./build/bin/llama-quantize --repack $M out_r4.gguf Q4_K (Syntax prüfen); nur -np 1
```
T9 HT:
```bash
numactl -N0 -m0 ./llama-bench ... -t 14,20,28
```

## 3) Engpass messen
```bash
PID=$(pgrep -f llama-server)
sudo perf top -p $PID
#  vec_dot_q4_K/dequantize oben → compute (ik_llama); ggml_barrier/futex → zu viele Threads; page faults → mmap/THP
sudo perf stat -e cycles,instructions,LLC-load-misses,LLC-loads -p $PID -- sleep 20
#  IPC>1.5 + wenig BW → Rechengrenze; IPC<0.7 + LLC-Misses → Remote/QPI → T1
sudo pcm-memory 1
sudo perf stat -a -e uncore_imc_0/cas_count_read/,uncore_imc_1/cas_count_read/ -- sleep 10   # ×64 B
numastat -p $PID
sudo perf stat -e node-loads,node-load-misses -p $PID -- sleep 10
```

## 4) Ziel
Ist 3,75 / 22 → T1+Kernel ~4,5–6 / 25–35 → +ik_llama ~6–8 / 40–70. Theorie ~11 (kaum erreichbar auf AVX2).
Option: 2× T1 parallel (je Socket eine Instanz hinter Load-Balancer) = doppelter Gesamtdurchsatz für Bots.

Reihenfolge: 0 → Kernel → T1 → T5 → T8 → perf → Rest. Pro Lauf t/s, IPC, GB/s pro Socket loggen.
