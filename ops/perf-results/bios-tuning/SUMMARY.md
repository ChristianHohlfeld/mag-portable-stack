# mag BIOS tuning 2026-10-16
syscfg 16.0.12 (Intel, Syscfg_V16.0.12_AllOS.zip), run unpacked from ~/bios-tuning/sc (not installed).
Backup: biosbak-full.INI (sha256 ae447878…), biosbak-bios.ini (/b). Restore: syscfg /r biosbak /b (or per-key /bcs back).
Set (/bcs): Enforce Recommended Memory Speed Enabled->Disabled(01); Memory Operating Speed Selection Auto->2400(0D);
CPU Power and Performance Policy Balanced Power->Performance(01); C1E Autopromote Enabled->Disabled(00); CPU C-State Enabled->Disabled(00). Turbo unchanged Enabled. HT/COD/Snoop untouched.
Reboot 09:16 CEST, SSH 09:19:46, llama ready 09:29:47. DIMMs 16x 2400 MT/s (before 2134). EDAC ce/ue 0, no MCE.
| | tg128 pp | tg t/s | TTFT | pp512 t/s | TTFT | cache 2nd |
|before|19.09|3.72|4.6|21.61|39.3|0.4|
|after |19.28|3.75|4.5|22.34|38.0|0.4|
3.6 ref: pp512 22.36, tg 3.70.
membw avg (MiB/5s) read 98937->96660, write 22323->24545; turbostat busy 43%, ~2809 MHz both.
