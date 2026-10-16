# Claude Runde 3.6 (Antwort, Basis 71975f6) — sha256 siehe Tarball (bc91e6ce…42e9)

Runde 3.6 ist fertig. Baut auf 71975f6, enthält Alis drei Journal-Punkte. Getestet nur auf nachgebautem 71975f6 (Tests grün; --install nur mit nachgebautem systemd). Auf mag noch nichts gelaufen.

## Fragen
- Echter Engpass: noch nicht belegt. no-mmap/Repack-Wette war falsch (0 %). Ausgeschlossen: Speicherlayout, NUMA (50/50), Takt/Governor, Bandbreite (19 %). Kandidaten: Thread-Sync nach jeder Op (28 Threads), viele kleine MoE-Matmuls, V4-Attention (neue Architektur, CPU-Pfade evtl. unoptimiert). Nur Profil klärt es: 3.6 erzeugt perf-Profil am laufenden llama (kein zweiter Prozess), Last per HTTP.
- VmLck 7,4 GiB: Vermutung: mlock sperrt nur normale Puffer; umsortierte Gewichte liegen in eigenem Puffertyp (CPU_REPACK, ~139 GiB) und werden nicht gesperrt. Prüfen: `journalctl -u mag-llm -b | grep -E 'model buffer size|mlock'`.
- Pin: sinnvoll, aber nur mit mmap. Mit no-mmap liegt das Modell doppelt im RAM. 3.6 stellt llama auf mmap+mlock um (eine Kopie, voll gesperrt, Neustarts aus RAM); mmap ist laut 3.5 gleich schnell. Pin bleibt.

## Alis Journal-Punkte (alle umgesetzt)
1. Cache-Bug Thinking: Raum sendet reasoning_effort nicht mehr (auch Drop-in); enable_thinking wirkt nur am Prompt-Ende → Umschalten kostet keinen Cache. Falscher 3.5-Kommentar korrigiert.
2. Abbruch stoppt llama: Gateway schließt Verbindung zu llama sobald Client weg (auch beim Prompt-Lesen); Log "aborted": true. 3 neue Tests. Grenze: llama prüft Abbruch erst nach Mikro-Batch (1024 Tokens ≈ bis ~45 s) → batch = ubatch.
3. Einreihen statt Abbrechen: neue Nachricht lässt laufende Antwort fertig werden, danach antwortet Bot mit neuer Nachricht im Verlauf; mehrere Nachrichten werden zusammengefasst. „Alle pausieren“ bricht weiter ab.

## `sudo bash apply.sh --install`
1. Vorprüfung: Pin aktiv und 100 % im RAM, Unit erzeugbar.
2. Gateway und Raum neu.
3. llama auf mmap+mlock; automatischer Rollback wenn nicht ready oder <130 GiB gesperrt.
4. Tuning per HTTP: Threads 28/24/20/16 (Generierung); threads-batch/ubatch 28/1024, 56/1024, 28/2048, 28/512 (Prompt). Bestes wird in /etc/mag-llm/llm.env gespeichert.
5. Benchmark vs. Alis Basis.
6. Profil nach /var/tmp/mag-profile-*.txt.
Dauer ca. 30–50 min; Raum zeigt zeitweise „Modell lädt“. `--rollback` stellt vorherige Unit wieder her. Patch ändert von Alis Dateien nur render-llm-unit.sh (zwei Einschübe, Parser unberührt).
Danach bitte /var/tmp/mag-tune-*.md und Profil zurückschicken → Runde 3.7.
