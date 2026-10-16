# Snapshot MAG DeepSeek-Stack 3.8 (Live-Stand 2026-10-16)
Ist-Abzug vom Server mag, ohne Secrets (gescannt).
- etc/: live /etc/mag-llm/llm.env, systemd-Units+Drop-ins (mag-llm, mag-120, mag-model-pin, mag-model-gate), sysctl (60-mag-llm, map-count, zeropage)
- ik-local/: ~/.local/lib/ik-llama (COMMIT, ikrun.sh, rollback.sh), lokale ik-Quellen ggml-moe-prefetch.* (untracked im ik-Tree 8d21687). Build-Rezept: ../build-ik.sh + ../ik-patches/
- bneck/: Benchmark-Skripte + Ergebnisse (res/, v38/var.sh, v38/results.tsv), kernel-dev (Q8_0/Broadwell q8_avx2.patch, mag_q8_patch.sh, kd.sh, results)
- scripts/go38.sh (aus /tmp), Apply: ../upgrade-3.8.sh
- mag-infra/: ~/mag-infra systemd-Backups inkl. BIOS-/Upgrade-Stände
Live: ik 8d21687, -rtr, MAG_NUMA_LOCAL=16, -t16 -tb28, b/ub 1024, f16 KV, THP aus: tg 7.67 / pp 49.3
