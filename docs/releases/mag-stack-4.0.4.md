# MAG-Stack 4.0.4 — schnellerer Kontext, robuste VM und Free MCP

**Release-Commit:** `51a1c95bb0c2b3ccd5fd096c0042c22ac9a5032a` (geprüfter `green`-Stand vom 20.10.2026).  
**Vorherige Version:** [MAG-Stack 4.0.3](https://github.com/ChristianHohlfeld/mag-portable-stack/releases/tag/mag-stack-4.0.3).

## Änderungen

- **Stabilerer Prompt-Cache für fortlaufende Gespräche** ([#83](https://github.com/ChristianHohlfeld/mag-portable-stack/pull/83)): Der Raumverlauf wird nicht mehr bei jeder Nachricht gleitend um einen kleinen Betrag verschoben. Stattdessen bleiben bereits berechnete Prompt-Anfänge über mehrere Runden identisch; bei Überschreitung der Grenze wird blockweise mit Reserve gekürzt. Regressionstests messen den gemeinsamen Präfix. Q4/IQ3 und laufende Modellprozesse bleiben unangetastet.
- **Browser und VM-Lebenszyklus korrigiert** ([#83](https://github.com/ChristianHohlfeld/mag-portable-stack/pull/83), [#84](https://github.com/ChristianHohlfeld/mag-portable-stack/pull/84)): Der einzige aktive Bot verwendet jetzt den kanonischen, persistenten VM-Start. VM-IP wird vorrangig hostseitig über libvirt ermittelt; dauerhaft ungültige Browser-/VM-IDs verursachen keine endlosen WebRTC-Wiederholungen mehr. Fehlt beim wiederhergestellten Bot ausdrücklich die VM-Metadatendatei, bleibt dessen alte VM-Datenablage geschützt, während für denselben Bot eine neue VM reserviert wird. Erinnerungen, Raumverlauf und Dateien werden nicht gelöscht.
- **Free MCP mit gültiger TLS-Prüfung** ([#81](https://github.com/ChristianHohlfeld/mag-portable-stack/pull/81), [#83](https://github.com/ChristianHohlfeld/mag-portable-stack/pull/83)): Das Prozessbild mit öffentlichem, nur lesbarem Link ist der Standardablauf. Der MCP-Client verwendet bei MAGs lokalem Audit-Proxy dessen kombiniertes CA-Bundle statt nur der öffentlichen CA. Das Deployment testet denselben Proxy-Pfad; Zertifikatsprüfung bleibt strikt eingeschaltet.
- **Sichere Wiederherstellung statt Datenverlust:** Der verwaiste vorherige VM-Name ist für erneute Vergabe gesperrt und auch vor automatischer Orphan-Bereinigung geschützt. Ein unerwarteter Berechtigungs- oder Prüffehler löst keine automatische Löschung aus.

## Performance – was belegt ist

Die bereits am **16.10.2026** gemessene Umstellung auf `ik_llama.cpp` verdoppelte die Prompt-Verarbeitung gegenüber `llama.cpp` ungefähr (19–22 auf 42,6–46,5 Tokens/s). Das ist eine **historische Messung**, keine neue Leistungsangabe für 4.0.4. Der Nutzen von 4.0.4 ist die **zuverlässigere Wiederverwendung bereits berechneter Tokens**; ein hardwarebezogener Vorher-/Nachher-Test des Cache-Hit-Anteils fehlt noch.

Quellen im Repository: [CPU-/Inferenz-Bottleneck-Report](https://github.com/ChristianHohlfeld/mag-portable-stack/blob/main/ops/perf-results/bottleneck/REPORT.md), [Prompt-Benchmark](https://github.com/ChristianHohlfeld/mag-portable-stack/blob/main/ops/perf-results/mag-httpbench-20261016-0654-3.6.md).

## Veröffentlichung und Rückweg

GitHub-CI und Promotion auf `green` waren vor der Markierung des Release-Commits erfolgreich. Die produktive Übernahme erfolgt **separat auf dem MAG-Host** über den vorhandenen Auto-Update-Timer. Ein GitHub-Release-Tag allein ist kein Beweis für den Serverstand. Das bestehende `ops/rollback.sh` kann den vorherigen Programmstand wiederherstellen; die großen `mag-llm`- und `mag-llm-iq3`-Instanzen werden vom Room-Deploy nicht neu gestartet.

**Live-Abnahme:** Prompt-Cache-Metriken `tokens_cached` / `tokens_evaluated`, erfolgreicher MCP-Handshake via MAG-Audit-Proxy, Browser-Steuerkanal offen, VM `run_command` erfolgreich und keine verbleibende Zuordnung zu fehlenden `metadata.json`.
