# MAG Unified Agent Stack — gemeinsame Arbeitsgrundlage (2026-10-13)

> **Status: Architektur-/Handoff-Contract; noch keine integrierte Live-Freigabe.** Beide ChatGPT-Arbeitsstränge verwenden dieses Dokument als gemeinsame, versionierte Quelle. Keine parallelen Installer oder widersprüchlichen Änderungen auf MAG.

## Ziel

Eine Modellinstanz auf dem Host; jeder Bot hat **seine eigene KVM-VM, einen eigenen Browser, eigenes Dateisystem und eigene Grok-Build-Sitzungen**. Bots sollen schnell aus einem **read-only Golden Image mit separatem qcow2-Copy-on-Write-Overlay** starten. `despawnbot` löscht nur den jeweiligen Bot und seine privaten Daten. Host bleibt jederzeit über Tailscale/SSH erreichbar.

## Ist-Stand (aus MAG-Terminalausgaben des Nutzers; keine aktuelle Remote-Prüfung)

- Ubuntu 24.04.4, Kernel 6.8.0-146, Intel VT-x aktiviert, /dev/kvm erreichbar.
- MAGBOT v0.2.0 installiert, VM-Smoke-Test erfolgreich: Ubuntu + Chromium + Playwright + Guest Agent + destruktionsfreies Cleanup.
- PM2 `magbot-watch` läuft, `pm2-chris` ist systemd-enabled; libvirt verwaltet VM-Lifecycle. Bereits gesicherter `agent1` wird/ wurde separat eingerichtet, letzter bekannter Status `installing`. Nicht ohne erneute Prüfung als `ready` annehmen.
- MAGBOT v0.4.0 ist **nur als separates ZIP vorbereitet**, auf MAG laut Nutzer **noch nicht angewendet**. `golden-adopt agent1` ist **noch nicht ausgeführt**.
- Grok Build CLI 1.0.52 und llama.cpp 0.6.0-dev sind auf dem MAG-Host installiert; `~/.grok/config.toml` und `deepseek.service` wurden im zweiten Chat vorbereitet. Zuletzt war `deepseek.service` disabled, die fünf GGUF-Shards wurden noch beschafft. **API-Bereitschaft nicht bestätigt.**
- Achtung: Nutzerangaben zeigen `ollama` zeitweise active trotz disabled; keine weiteren Runtime-Start-/Stop-Kommandos ohne Prüfung der aktiven Ports und laufender Downloads.

## Architektur und Zuständigkeiten

| Komponente | Eigentümer/Lifecycle | Daten/Netz |
|---|---|---|
| llama.cpp + DeepSeek GGUF | `systemd deepseek.service` (Inference-Arbeitsstrang) | **Eine** Modellinstanz auf Host; GGUF ausschließlich `~/models/deepseek-v4`; derzeit `127.0.0.1:8080/v1` |
| Geschütztes Inference-Gateway (noch zu implementieren) | gemeinsamer Integrationsstrang | Authentifiziertes OpenAI-kompatibles API nur über dedizierte VM-Netz-Schnittstelle/vsock, keine Freigabe ins WAN oder ganze LAN |
| Grok Build Agent-Harness | **je Bot innerhalb seiner VM**, nicht wiederholt auf Host gestartet | Separate Session/Tools/Secrets pro VM; API base_url zeigt auf Gateway, nicht auf `127.0.0.1` des Gasts |
| Browser und Dateisystem | libvirt/KVM pro Bot | Chromium + Playwright in Golden Image, isolierte Overlay-Daten pro Bot |
| MAGBOT CLI | root-gesteuerter Host-Lifecycle | `spawnbot`/`despawnbot`/Status/Logs/Quota; kein zweiter Orchestrator |
| PM2 `magbot-watch` | `pm2-chris` nach Hostboot | Rootloser **read-only** Monitor; erstellt/vernichtet/restartet keine VMs |
| libvirt | systemd/libvirt Autostart | Einzelne Bot-VMs nach Host-Neustart, kein Duplikat-Spawn |
| SSH/Tailscale | Host unabhängig von Bots | Muss auch bei Maximal-Last erreichbar bleiben |

### Wichtiger Netzkonflikt

Ein aus der VM aufgerufenes `127.0.0.1:8080` ist **die VM selbst**, nicht MAG. Den llama-Server nicht einfach auf `0.0.0.0` öffnen! Ein Gateway mit Auth/Access Control muss zwischen VM und loopback-only Inference-API liegen. Bot-Egress zu privatem LAN, Hostdiensten, Tailscale und Link-Local wird standardmäßig verweigert; nur ausdrücklich freigeschaltetes Gateway und erlaubtes öffentliches Internet. IPv4 und IPv6 testen.

### Ressourcen / Admission

Default MAGBOT v0.4: maximal 4 Bots, insgesamt 8 vCPUs und 24 GiB zugesagter VM-RAM; je Bot 2 vCPU / 4 GiB / 20 GiB thin-provisioned Disk. Mindestens 100 GiB tatsächlichen freien Host-Datenträger und 64 GiB `MemAvailable` nach VM-Start, plus Last- und I/O-Caps. **Vor Start der ~155-GB-DeepSeek-Inferenz** zusätzlich den zu erwartenden Model-RAM-Bedarf reservieren; bloß die momentan freien ~500 GiB dürfen nicht als dauerhaft verfügbare Bot-Kapazität behandelt werden. Limits bei `spawn` immer fail-closed.

### Golden Image / State Lifecycle

1. `agent1` zu `ready` bringen und `health.py` erfolgreich testen.
2. Erst dann `golden-adopt agent1 --allow-restart` **in einem Zeitfenster ohne laufende Bot-Jobs** erwägen: kontrollierter Gast-Shutdown, konsistente **separate** Kopie, Original wieder booten. Keine Kopie von Tokens, Browserprofilen oder Credentials; nach Prüfung nur sauberes, immutable Image freigeben.
3. `v0.4`-Golden Image enthält **noch kein Grok Build pro VM und kein Gateway**; nach Integration neue **versionierte** Golden-Image-Version erstellen, bestehende Backing-Datei niemals überschreiben.
4. `spawn` = Admission Check, individuelles qcow2 Overlay + Cloud-init-ID/SSH keys/browser profile, VM-Boot, Guest-Agent-Handshake, Tests, `ready`. **Keine** Ubuntu-, pip- oder Chromium-Downloads pro Bot.
5. `despawn` = stoppen, libvirt undefine und Autostart entfernen, eigene ISO/Overlay/Metadaten/Secrets entfernen; nie die Golden-Backing-Datei oder andere Bots löschen.
6. Stuck-Boot/Guest-Agent: zuerst `booting`/`waiting_for_guest_agent`; Timeout mit Fehlersignal und hostseitigem Diagnosepfad. Kein Auto-Wipe.

## Arbeitsaufteilung — keine konkurrierenden Host-Mutationen

**Inference-Arbeitsstrang (anderer Chat):** Nur GGUF-Shards, llama.cpp-Benchmark/Unit, Host-only Modell-API, Grok-Build-Verhalten und Konfiguration des lokalen Host-Clients. Liefert belegte `/v1/models`, `/v1/chat/completions`/Tool-Call-Smoke-Tests und tatsächlichen Peak-RAM/CPU-Bedarf. Keine MAGBOT-Installer/VM-, Netzwerk- oder PM2-Änderungen.

**Sandbox-/Integrations-Arbeitsstrang (dieser Chat):** MAGBOT v0.4 Source, sichere Golden-Image-Wiederverwendung, VM-Netz/Authentifizierung, Gast-Grok-Image, Spawn/Despawn-Schutz, Ressourcenbudget, PM2/Autostart-Verifikation. Keine Änderung des DeepSeek-Modells, llama-Cmdlines oder `~/.grok/config.toml` auf dem Host.

**Gemeinsamer Contract:** REST/OpenAI-API, Modellkennung, Gateway-Adresse nur VM-intern erreichbar, Authmechanismus, Timeouts/Rate-Limits, zulässige concurrency; Änderung nur via Commit/PR + gemeinsamen Handoff.

## Release-Gates (alle erforderlich)

- Host nach Live-Reboot über Tailscale und SSH erreichbar; `/dev/kvm` vorhanden.
- Model-API erfolgreich aus Host UND einem Test-Gast erreichbar, API-Zugriff außerhalb des Gasts abgewiesen.
- DNS und private Route geprüft: VM erreicht **keine** nicht freigeschalteten MAG-/LAN-/Tailscale-/Metadata-Endpunkte; IPv6 abgedeckt.
- Fresh agent from new Golden Image: keine Browserdownloads; Grok Build + Playwright-Health erfolgreich, Tool-Calls an genau eine lokale Modellinstanz.
- Parallel 4 Bots unter Limits, Host-RAM/CPU/Disk und SSH/Tailscale bleiben stabil; Start des 5. Bots fail-closed.
- `despawnbot` löscht nur target VM/Overlay/ISO/Identity und verhindert Autostart; gemeinsames Golden Image bleibt nutzbar.
- Persistenz nach **kontrolliertem** Reboot: PM2 Monitor + libvirt VMs ohne Doppelstart, keine Session-/Secret-Kreuzkontamination.

## Aktuelle Regel für Bedienung

**MAGBOT v0.4.0 noch nicht ausführen**, bis die beiden Stränge die Modell-API, Gateway-Zugangsregeln und Guest-Grok-Startstrategie abgestimmt haben. Andere laufende Download-/Inference-Vorgänge nicht abbrechen. Erst `sudo magbot status agent1` und laufende Hostdienste *lesend* prüfen.

## Übergabe in den anderen Chat

> Wir bauen einen MAG-Gesamtstack mit **einer einzigen DeepSeek-/llama.cpp-Inferenz auf dem Host** und **separaten KVM-Bot-VMs**. Bitte arbeite ausschließlich am Inference-Pfad (5 GGUF-Shards, Benchmarks, systemd, Grok Build Host-Probe). **Ändere weder MAGBOT/libvirt/PM2 noch Host-Netzregeln.** Sende nach Fertigstellung: exakte `/v1/models` Modell-ID, Host-Port, Beispiel für erfolgreichen Chat/Tool-Call, peak RAM/CPU und benötigte Auth-/API-Konventionen. MAGBOT v0.4 Golden-Image ist bislang nur vorbereitet und wird vor Integration nicht auf MAG ausgeführt. Gemeinsamer Plan im selben Repository: `docs/MAG_AGENT_STACK_CONTRACT.md`.
