# MAG: IQ3 parallel auf NUMA 1 (Stand 17.10.2026)

**Entscheidung:** Q4 bleibt das einzige aktive Modell für MAG-Raum, Grok und Ali; seine aktuelle `mag-llm.service` läuft unverändert auf NUMA0 (lokale CPUs + Speicher). IQ3 ist eine **separate, dauerhaft resident-fähige** Instanz nur auf NUMA1. **Kein Hot-Swap, keine Umschalt-API.** Die frühere PR #31 wurde bereits gemergt; dieser PR deaktiviert die gefährliche sequenzielle Stop/Start-Steuerung.

**NUMA-Kapazität:** Zwei Knoten mit je ca. 252 GiB. Q4 ~145 GiB auf Knoten 0. IQ3 ~104 GiB (dezimal, vier GGUF-Dateien) auf Knoten 1 + 16 GiB Promptcache + KV/Runtime. IQ3 hat `MemoryHigh=155G` und `MemoryMax=176G`. Bei Preflight müssen auf NUMA1 mindestens 188 GiB freie/reclaimbare Seiten verfügbar sein. Knoten 1 behält damit nach Modellinitialisierung erheblichen Spielraum für Linux und den Restic-Backup-/Restore-Job.

**CPU/Backup:** IQ3 bindet 12 von 14 physischen Node1-Kernen (logisch 14–25,42–53) und `--threads 12 --threads-batch 12`; Restic aus PR #35 verwendet denselben Knoten (Nice=15, ionice idle, 03:30 Berlin). IQ3 nutzt `Nice=10`, `IOSchedulingClass=idle`. Start und SHA-Leseprüfung erfolgen nicht, während ein MAG-Backup läuft. Die Backup-Jobs behalten ihre eigene `flock`-Serialisierung. Beim eigentlichen Modellstart werden ca. 104 GB von der lokalen Platte gelesen: auf einem gemeinsamen Datenträger können Q4-Diskzugriffe vorübergehend langsamer werden. **Q4-Prozess und resident gespeicherte Gewichte werden dabei nicht angehalten, neu geladen oder verlagert.** Vor Host-Start `iostat -xz 1`, `numastat -p`, `free -h` und `systemctl list-jobs` prüfen; nicht um 03:30 starten. Zusätzliche I/O-Grenzen nur nach gemessener SSD-Last.

**SHA:** `ops/deepseek/iq3.sha256` enthält die vier **offiziellen** SHA256-Digests des Hugging-Face-Repos `unsloth/DeepSeek-V4-Flash-0731-GGUF` (Ordner `UD-IQ3_XXS`). Die Dateien und das lokale `SHA256SUMS` auf dem Host müssen zu diesem *unabhängig versionierten* Manifest passen. Split 1 ist nur ~5,26 MB, darf also nicht durch die alte ">1GB pro Split"-Regel blockiert werden. Ein einziger Fehler bedeutet fail-closed. Vor Aktivierung vollständige einmalige Hashprüfung (~104 GB I/O) mit niedriger I/O-Priorität. Prüfungen bei jedem service start laufen nochmals.

**Eingänge/Isolation:** :8090 bleibt Q4. IQ3 bindet **ausschließlich localhost :8091**; nftables `mag_audit` begrenzt direkte Verbindungen auf root/mag-audit, kein nginx- oder Gateway-Pfad zu IQ3. Das native C++-Gateway :8080 routet immer Q4; die alte Owner-`/api/models`-Mutation gibt 410 zurück, das Bedienelement ist entfernt. IQ3 startet nicht automatisch beim Deploy.

**Ali – nach Merge:**

```bash
cmake -S . -B build -G Ninja -DCMAKE_BUILD_TYPE=Release && cmake --build build -j 4
sudo bash ops/deepseek/iq3-parallel.sh preflight
# Beim nächsten ruhigen Gateway-Cutover (unterbricht ggf. gerade laufenden Antwortstream):
sudo bash ops/deepseek/iq3-parallel.sh apply
# Erst dann explizit durch Ali:
sudo systemctl start mag-llm-iq3.service
sudo systemctl status mag-llm-iq3.service
curl --noproxy '*' -fsS http://127.0.0.1:8091/health
sudo numastat -p $(systemctl show -p MainPID --value mag-llm-iq3.service)
```

`apply` installiert den Q4-only C++-Gateway, deaktiviert die alte Modellwechsel-Supervisor-Unit, beseitigt ausschließlich den alten IQ3-Start-Veto-Drop-in und bereitet die neue IQ3-Unit vor. **Weder `systemctl stop mag-llm`, `restart mag-llm`, noch irgendein Q4-Modellwechsel erfolgt.** Vor und nach der Gateway-Migration wird der Q4-MainPID geprüft. Ein Gateway-Neustart ist einmalig nötig, um die schon gemergte Hot-Swap-Routing-Logik aus dem laufenden Prozess zu entfernen; er sollte außerhalb aktiver Streams erfolgen. Der Raum-Chat bleibt auf Platte bestehen.

**Rollback:** `sudo bash ops/deepseek/iq3-parallel.sh rollback` stoppt nur IQ3, restauriert die vorherigen Gateway-Unit/Binärdatei sowie gespeicherte Guards und startet das Audit-Gateway neu. Q4 bleibt unverändert. Da die alte Gateway-Version von einem Supervisor abhängig sein kann, darf das Rollback vorübergehend die *alte* Supervisor-Unit wieder aktivieren; **keinen Modellwechsel auslösen**. Für vollständige Abschaffung der alten Unit den Q4-only Gateway erneut installieren.

**CI:** Native MAG und C++-Audit, Web/Guest/Pages, SHA-Fixtures (inkl. 5-MB-artigem Split), manipulierte Digests, fehlende/zusätzliche Dateien, Bootstrap-Shellsyntax und NUMA1-Bindungsinvarianten. Keine neuen Node-/Python-Runtime-Dienste, keine Secrets.
