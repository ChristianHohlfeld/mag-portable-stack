# Claude: Q8_0/Barrier-Plan für mag (2026-10-16 ~10:05)
Chat: https://claude.ai/chat/996029f7-f120-4761-9efa-657c32177fe2
Skript: /workspace/claude/mag_speed.sh (209 Zeilen, Syntax geprüft, auf mag noch nie gelaufen)

Diagnose: Claude stimmt zu. Q8_0-Dot (dichte Tensoren, 8,5 bit, kein AVX2-Repack) ist der größte Hebel, Barrier 17-20 % = Sync-Overhead bei Batch 1, MXFP4 kaum Potenzial.
Vorab prüfen, ob Rechnen oder Latenz bremst:
  sudo perf record -g -p $PID -- sleep 15 && sudo perf annotate ggml_vec_dot_q8_0_q8_0
  sudo perf stat -e cycles,instructions,cycle_activity.stalls_l3_miss -p $PID -- sleep 15
  (IPC>1,5 = Rechnen -> Repack zuerst; IPC<0,8 = Latenz -> Requant zuerst)

Hebel:
A) ik_llama -rtr (Q8_0 -> Q8_0_R8) + -fmoe, +20-40 %; im Log auf Repack prüfen
B) dichte Q8_0 -> Q6_K requantisieren, MXFP4-Experten bleiben, +10-20 %, Perplexity höchstens +1 %
C) Barrier: OMP_WAIT_POLICY=active OMP_PROC_BIND=close OMP_PLACES=cores, -t 14 strikt gepinnt bzw. --cpu-strict 1 --poll 100, +5-15 %

Realistisch laut Claude: 4,5-6 t/s (frühere Schätzung 6-8 korrigiert).

Ablauf:
  export M=... MQ=...
  sudo ./mag_speed.sh prep; ./mag_speed.sh build; base; threads; ik; types; requant; ikq; ppl; report
Neustart erst bei >=15 % (Skript markiert das als GEWINN).

ACHTUNG (Stabschef): Im Startbefehl von `report` stehen -ctk q8_0 -ctv q8_0. Bei V4 liefert der quantisierte K-Cache Unsinn (llama.cpp #25382). Beim Übernehmen weglassen bzw. f16 nehmen.
Danach results.tsv an Claude zurück, dann justiert sie -t und -ub.
