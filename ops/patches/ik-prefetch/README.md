# mag ik-Prefetch (auf Runde 3.8)

Software-Prefetch in den AVX2-Kernels, die bei 3.8 die Generierung tragen:
`mul_mat_q8_0_r8_q8_2_avx2` (Q8_0_R8, dichte Tensoren) und `mul_mat_mxfp4_r8_q8_2_avx2` (MXFP4_R8, Experten).
Jeder Thread lädt die Gewichtsblöcke 4096 B vor dem gerade gelesenen Block; Broadwells L2-Streamer bricht
an jeder 4-KiB-Seitengrenze ab. Die Rechnung bleibt bitgleich, nur der Speicherzugriff ändert sich.

- Patch: `0003-iqk-prefetch.patch` gegen ik_llama.cpp `8d21687cc7358de10264d4180dba9b863024d5c4`,
  setzt auf 3.8s `0001-server-prompt-progress` und `0002-numa-local` auf (geprüft: `git am` sauber,
  kompiliert warnungsfrei mit Broadwell-Flags, `prefetcht0` im Objekt, keine AVX-512-Befehle).
- Distanz: `GGML_Q8_PF` / `GGML_MXFP4_PF` in Bytes, Standard 4096, `0` = aus (einmal beim ersten Aufruf gelesen).
- Passt zum Live-Setup: `-rtr`, `MAG_NUMA_LOCAL=16`, `-t16/-tb28`, ub 1024, f16-KV bleiben unverändert –
  `apply.sh` tauscht nur `IK_BIN` und rendert die Unit mit der bestehenden `llm.env` neu.

```
sudo bash apply.sh                            # Build neben dem laufenden Modell, Umstellung, Neustart aus dem RAM, Schnelltest
sudo Q8_PF=8192 MXFP4_PF=8192 bash apply.sh   # andere Distanz (Drop-in 61-mag-prefetch.conf)
sudo bash rollback.sh                         # Unit, llm.env, Drop-in, ik-patches/0003 zurück
```

Kein `drop_caches`, kein `--membind`, kein `-ctk q8_0`, Port 8080 bleibt unberührt.
Fällt der Neustart oder der Schnelltest durch, ruft `apply.sh` selbst `rollback.sh` auf.
Nach `apply.sh` liegt `ops/deepseek/ik-patches/0003-iqk-prefetch.patch` uncommittet im Repo – bei Gefallen committen.
