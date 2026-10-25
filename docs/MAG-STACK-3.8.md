# MAG-Stack 3.8 – Gesamtstand (Tag `mag-stack-3.8`, 16.10.2026)

Ein Dokument für den ganzen Server-Stack: Hardware, Dienste, LLM-Engine, RAM-Regeln, Sandbox/Browser,
Raum-Frontend, git-memory-MCP, bekannte Bugs und Rollback. Live-Abzüge der Konfiguration liegen in
`ops/deepseek/snapshot-3.8/` (LLM-Seite) und `ops/browser-stack/live/` (Browser/Audit-Seite).
**Keine Secrets im Repo** (gitleaks/Muster-Scan vor jedem Push; Zugangsdaten bleiben in /etc bzw. in der VM).

## Hardware
| | |
|---|---|
| Board/BIOS | Intel S2600CWR (SE5C610.86B.01.01.3029, 24.02.2022) |
| CPU | 2× Xeon E5-2680 v4 (Broadwell-EP), 56 Threads, 2 NUMA-Knoten |
| RAM | 512 GB DDR4, 16 DIMMs, **2400 MT/s** (im BIOS gesetzt, vorher niedriger getaktet) |
| Modell | DeepSeek-V4-Flash-0731 UD-Q4_K_XL (5 GGUF-Teile, ~151 GB, komplett gelockt) |

## Dienste und Ports
| Dienst | Port | Zweck |
|---|---|---|
| `mag-llm` (ik_llama `llama-server`) | 127.0.0.1:8090 | Modell; per nft nur für den Audit-Gateway erreichbar |
| Audit-Gateway | 127.0.0.1:8080 | einziger Weg der Bots zum Modell (Log) |
| `mag-120` (magroom, nativ C++) | 127.0.0.1:8766 | Raum-UI/API; VM-Bridge 192.168.122.1:8767 |
| `mag-audit-webproxy` (mitmproxy 11.1.3) | :8888 | Shell/Git/Tools aus den VMs, **voll auditiert** (MITM, Hash-Kette) |
| dto. | :8889 | **nur Bot-Browser**: CONNECT-Tunnel ohne TLS-Aufbruch, Log nur Host/SNI, Blockliste |
| Gitea | 192.168.122.1:3001 | Bot-Repos (inkl. `memory`) |
| `mag-model-pin` | – | vmtouch `-l` (Modell bleibt im RAM), numactl interleave |
| `mag-model-gate` (+timer) | – | Modell-Integrität |
| Tailnet-Zugang | 100.123.114.16:443 | `ops/private-access` (Caddy, nur Tailnet) |
Tests nie auf 8080/8090.

## Engine und Flags (live)
- ik_llama 8d21687 + lokale Patches (`ops/deepseek/ik-patches/0001–0003`, Build `ops/deepseek/build-ik.sh`)
- `--load-mode mmap+mlock`, `-rtr`, `-t 16 -tb 28`, `-b/-ub 1024`, `--parallel 1`, KV-Cache **f16**
- `IK_ENV="MAG_NUMA_LOCAL=16 GGML_Q8_PF=4096 GGML_MXFP4_PF=4096"`
- Startwerte: `/etc/mag-llm/llm.env` (von `var.sh` geschrieben), Unit via `ops/deepseek/render-ik-unit.sh`
- **THP für mag-llm aus** (`MAG_THP=1` entfernt; System `madvise`), sysctl `60-mag-llm`, `10-map-count`, `10-zeropage`
- Bench/Varianten: `snapshot-3.8/bneck/v38/var.sh`, `results.tsv`, `scripts/go38.sh`, Apply `ops/deepseek/upgrade-3.8.sh`

### Zahlen (Generierung tg / Prompt pp, t/s)
| Stand | tg | pp |
|---|---|---|
| Ausgang | 3.75 | ~22 |
| Zwischenstand (ik_llama) | 6.5 | – |
| 3.8 (RAM 2400, -rtr, Prefetch, THP aus) | **7.87** (Messung nach THP-off: 7.67) | **~50** (49.3) |

## RAM-Regeln (absolut)
1. Das Modell verlässt **nie** den RAM: Pin (`mag-model-pin`, mlock) bleibt an, **nie** `drop_caches`.
2. Immer **≥ 40 GiB MemAvailable**.
3. `mag-llm` nicht leichtfertig neu starten (RefuseManualStop; Restart nur über temporäres /run-Drop-in wie in `upgrade-3.8.sh`).

## Sandbox / Bot-Browser
- Ein Bot mit eigener KVM-VM (`MAG_ROOM_SINGLE_BOT_VM=on`, Drop-in `ops/mag-120.d/20-room-audit.conf`): Shell, git/Gitea, MCP, Browser.
- **Google Chrome stable** aus Googles signiertem apt-Repo (Rezept + AppArmor-Profil `mag-google-chrome` in `mag-room/native/vm.cpp::prepare_browser`).
- Headful auf **Xvfb :99 1920×1080** (vom `mag-browser`-Daemon gestartet, kein eigener Unit), `--lang/--accept-lang de-DE`, `TZ=Europe/Berlin`, kein `--kiosk`, kein `--enable-automation`; CSS-Viewport 1440×900 (Live-Stream/Koordinaten).
- CDP ohne `Runtime.enable` (Auswertung in `Page.createIsolatedWorld`), Navigationslog `/home/bot/browserd/nav.log` (Status, Titel, cf-ray, x-datadome, `frame=main|sub`).
- Proxy-Trennung: Browser-Policy → `:8889` (Passthrough, `ops/audit/mitm_audit_addon.py::BrowserPassthrough`), alles andere → `:8888` (voll auditiert). nft: `ops/mag-agents.nft`, `ops/audit/mag-audit.nft`. Optional IPv4-Egress `MAG_BROWSER_EGRESS=ipv4` (Default aus).
- Captcha-Regel im Prompt: keine Captchas lösen, Nutzer fragen.
- Rollback: `sudo ops/audit/browser-passthrough-rollback.sh /var/backups/mag-browser-pt-<ts>`.
- Detektoren: rebrowser alles grün, sannysoft grün bis auf SwiftShader-WebGL (ehrlich, nicht gefälscht). Google-Suche liefert derzeit `/sorry` (429) – sowohl über IPv6 als auch IPv4: IP-Reputation, kein Browser-Fingerprint-Problem.

## Raum (Frontend/API)
- `/bot/<slug>` → 302 `/?bot=<slug>` (eine Oberfläche); Nav-Leiste, Live-Cursor-Overlay, HTML-Auto-Open (`?view=1`, CSP sandbox).
- Raumdateien folgen dem VM-Dateisystem (`MAG_ROOM_FILE_SYNC_SECONDS`, Default 10): gelöscht/umbenannt → „entfernt“ (404), geändert → neu, neu in `/home/bot/room` → erscheint.
- Theme: Stardenburg (PR #14).

## git-memory MCP
`ops/mcp/git-memory/` – Python (nur stdlib), MCP über stdio + CLI `gitmem`:
`remember(key,value)` (commit + push), `retrieve`, `history`, `search`. Speicher `~/memory`, Backup privates Gitea-Repo
`<bot>/memory` (wird angelegt, bei neuer VM zurückgeholt). Installation: `sudo ops/mcp/git-memory/install-vm.sh magbot-agent1`. Tests: `test_gitmem.py`.

## Bekannte Bugs (llama.cpp/ik, DeepSeek V4)
- **#25382**: quantisierter K-Cache (q8_0) liefert Unsinn → KV-Cache f16.
- **#25259**: Prompt-Cache verliert Kontext ohne `--parallel 1` → `--parallel 1`.

## Rollback
- Raum/Browser: `/opt/mag-stack/current` → vorheriges Release in `/opt/mag-stack/releases/` (Symlink-Tausch, `systemctl restart mag-120`).
- Browser-Proxy: `ops/audit/browser-passthrough-rollback.sh`.
- LLM: `ops/deepseek/snapshot-3.8/ik-local/rollback.sh`, `ops/deepseek/ik/rollback.sh`; THP: `*.bak-thp-20261016104505`.
- RAM-Takt: BIOS (Memory Configuration) zurück auf vorherigen Wert.
