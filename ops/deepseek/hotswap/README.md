# MAG Hot-Swap: exklusiv NUMA-Knoten 0

**Abhängigkeit:** PR #33 (NUMA0-Isolation) muss zuerst gemergt, gestaged und nach *explizitem* Q4-Neustart durch Ali verifiziert sein. Q4 und IQ3 sind mit 147 + 97 GiB nicht gleichzeitig auf Node 0 (ca. 252 GiB) betreibbar. **Kein Parallelstart**, Knoten 1 bleibt frei.

```text
MAG-Raum :8766   ──► C++ Audit :8080 ──► jeweils genau EIN laufendes Modell
Grok/Ali (8080) ──►       │              Q4 :8090 oder IQ3 :8091
Owner /api/models ──► C++ Raum ──► control.sock ──► Gateway
                                            │
                                   C++ Supervisor (root, SO_PEERCRED)
                                            │
                                  stop alt -> PID weg -> Node0-Memory frei
                                  -> start neu -> NUMA + /health gate
                                  -> Commit oder Recovery alt
```

Ein eigener C++-Supervisor (systemd, Root, Unix-Socket nur für `mag-audit`) hat ausschließlich `systemctl stop/start` für die beiden fest verdrahteten Modell-Units erlaubt. Das **Audit-Gateway läuft weiter ohne Rootrechte** und wählt anhand der Root-Supervisor-Auskunft den aktiven Loopback-Upstream. Der Client darf keine systemd-Unit oder beliebige Argumente übermitteln. Gateway-Audit inkl. SSE/Reasoning/Tool-Calls bleibt bestehen; nginx bleibt der einzige Webproxy.

**Transaktion:** Ein Owner darf den Wechsel nur starten, wenn keine Inferenz läuft. Die laufende Anfrage wird andernfalls nicht abgebrochen; Besitzer bekommt eine klare Meldung. Während des Ladens zeigt der Raum seine vorhandene Modell-Ladeanzeige, neue Inferenzanfragen warten bis zu 30 Minuten (danach 503 mit deutschem Hinweis), die Chat-/Browser-Session bleibt erhalten. Keine Statusnachrichten im Chat. Erst nach verifiziertem /health **und** CPU-/Speicherbindung auf NUMA0 wird `active` atomar persistiert. Bei Fehlschlag: Ziel stoppen, vollständiges Entladen prüfen, altes Modell neu starten und gesund prüfen, dann die alte Auswahl zurücksetzen. Bleibt auch die Wiederherstellung erfolglos, fail-closed (kein falsches „bereit“).

**Cache:** Q4 verwendet den bereits existierenden Slot-Pfad unter `/var/lib/mag-llm/slots`, IQ3 seinen eigenen Unterordner `/var/lib/mag-llm/slots/iq3` (eigener `--cache-ram 16384`-Cache je Prozess, niemals gleichzeitig resident). Die Prompt-/Memory-Daten im Raum bleiben unverändert; flüchtige Cache-Metriken und Restore-Flags werden nach Auswahlwechsel invalidiert. Die persistente Modellwahl liegt in `/var/lib/mag-model-supervisor/selector.json` mit atomarem Schreiben/Fsync.

Beim Staging wird die #33-IQ3-Startblockade gesichert und durch die sequenziell kontrollierte IQ3-Unit ersetzt; Rollback stellt die Blockade wieder her. Ein direktes `systemctl start mag-llm-iq3` darf Q4 nie implizit stoppen (kein `Conflicts=`). IQ3-Start ist durch SHA256SUMS über **alle vier** Split-Dateien, gesperrte Slot-Verzeichnisse, exklusiven Prozess und `numactl --cpunodebind=0 --membind=0` + systemd `CPUAffinity`/`NUMAPolicy=bind` geschützt. Q4 bezieht seine entsprechende Isolation unverändert aus #33. Doppel-Lock `mag-model-pin` wird **allein** durch #33 abgeschaltet. Keine neue Python-/Node-Laufzeit.

Siehe [OPERATIONS](OPERATIONS.md) und [ROLLBACK](ROLLBACK.md).
