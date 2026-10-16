# DeepSeek-V4-Flash-0731 auf mag + zentrales Audit-Logging

Ersetzt GPT-OSS-120B durch DeepSeek-V4-Flash-0731 (unsloth UD-Q4_K_XL, ~155 GB, CPU) im
bestehenden Stack und leitet alle LLM-, Werkzeug- und Web-Zugriffe der Bots über eine zentrale,
dauerhaft protokollierende Schnittstelle.

```
Raum (magroom) ─┐                                        ┌─> llama serve 127.0.0.1:8090 (mag-llm)
Grok / Ali ─────┼─> mag-audit-gateway 127.0.0.1:8080 ────┘   nftables: nur root + mag-audit
                │      └─ /var/log/mag-audit/YYYY/MM/DD/events.jsonl (Prompt, Reasoning, Tool-Calls+Ergebnisse)
Agent-VMs ──────┼─> mag-audit-webproxy 192.168.122.1:8888 ─> Internet   (VMs: kein direktes 80/443)
Raum (curl) ────┘      └─ /var/log/mag-audit/YYYY/MM/DD/web.jsonl + blobs/
```

## Komponenten

| Datei | Zweck |
|---|---|
| `mag-room/native/*` | magroom: DeepSeek-Defaults, `chat_template_kwargs` (enable_thinking, reasoning_effort high), LLM-Timeout ≥ 30 min, `reasoning_content` im Tool-Verlauf, Modellstatus (`/api/model-status`, SSE `model`, `/readyz` + `/readyz/model`), Warten beim Laden, Chat-Bots ohne VM; `magbot audit-proxy` (vm.cpp) |
| `mag-room/web/index.html` | Modellstatus im Header + Hinweisleiste, „Nur Chat (ohne VM)“ |
| `ops/mag-llm.service` | llama serve, DeepSeek, :8090, Start nur mit Freigabe-Stempel |
| `ops/mag-model-gate.{service,timer}`, `ops/deepseek/model-gate.sh` | Download beobachten → SHA-256 prüfen → mag-llm starten; Status für den Raum |
| `ops/audit/*` | Gateway, mitmproxy-Addon, nftables, Installation, VM-Gast-Skript, `mag-audit-tail` |
| `ops/mag-120.d/20-deepseek-audit.conf` | Raum-Umgebung: Backend, Modell, Denken, Timeouts, Proxy |
| `ops/magbot-guest.sudoers` | `Defaults:magbot !syslog` – Token/UDP-Schlüssel nicht mehr im Journal |
| `ops/security/*` | Rotation, Leak-Suche, Journal-Bereinigung |
| `ops/health.sh`, `ops/deploy.sh` | Modell lädt = Warnung statt Fehler; Deploy unabhängig vom Modell |

## Timeouts

LLM-Anfragen: `MAG_ROOM_LLM_TIMEOUT=3600` s, im Code nie unter 1800 s; Gateway → Backend 7200 s.
Gast-Befehle (`run_command`): `MAG_ROOM_GUEST_TIMEOUT=1800` s (Sekunden, `guest_command`).
Probes und Verbindungsaufbau bleiben kurz (Health 5 s, Connect 5 s, `/health`-Probe 2 s).

## Betrieb

- Status: `sudo bash ops/deepseek/rollout.sh status`, im Raum oben rechts.
- Audit-Log lesen (Gruppe `mag-audit-readers`): `mag-audit-tail -f`, `mag-audit-tail --verify`.
- mag-llm stoppen/neu starten (RefuseManualStop): `sudo bash ops/deepseek/mag-llm-maint stop|restart`.
- Modell neu prüfen erzwingen: `sudo rm /var/lib/mag-llm/verified.stamp && sudo systemctl start mag-model-gate`.

## Engine ab 3.8: ik_llama.cpp, auf mag zugeschnitten

`ENGINE=ik` in `/etc/mag-llm/llm.env` (gesetzt von `upgrade-3.8.sh`) schaltet mag-llm von llama.cpp auf
ik_llama.cpp um – gleiches Modell, gleiche GGUF-Dateien, gleiche Vorlage `ops/mag-llm.service`
(`render-ik-unit.sh` ersetzt nur ExecStart). `ENGINE=-` (bzw. `upgrade-3.8.sh --rollback`) schaltet zurück.

| Baustein | Wirkung auf mag (2x E5-2680 v4, AVX2, 2 NUMA-Knoten) |
|---|---|
| `build-ik.sh` | fester ik-Stand + `ik-patches/`, GCC, `GGML_NATIVE` (Broadwell), mit Symbolen |
| `-rtr` | Q8_0 → Q8_0_R8, MXFP4 → MXFP4_R8: AVX2-Kernels für 16 Zeilen je Durchgang (mainline: kein Q8_0-Repack auf x86) |
| `0002-numa-local.patch`, `MAG_NUMA_LOCAL=<t>` | Zeilenblock *i* jeder Gewichtsmatrix (auch jedes Experten) liegt im RAM des Sockels, auf dem Thread *i* läuft; Threads blockweise je Sockel auf feste Kerne; Experten statisch statt dynamisch verteilt; Lokalität wird nach dem Laden gemessen (`journalctl -u mag-llm | grep mag_numa`) |
| `MAG_THP=1` | Huge Pages für die Gewichte (2-MiB-Raster) |
| `-muge` | up/gate der Experten in einem Tensor |
| `0001-server-prompt-progress.patch` | `prompt_progress` im Stream → Fortschrittsanzeige im Raum |

Welche Kombination läuft, entscheidet `upgrade-3.8.sh` per HTTP-A/B (nur übernommen, wenn ≥ 5 % besser
als vorher und die Generierung nicht langsamer, danach Go/No-Go mit `preflight-tests.py`).
