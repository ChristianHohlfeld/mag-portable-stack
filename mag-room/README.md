# MAG Room

The production room is native C++17. See the [stack README](../README.md).

- `native/`: room, CDP browser and VM lifecycle binaries.
- `web/`: default frontend.
- `static/`: locally served Markdown/rendering assets.
- `tests/`: isolated native regression tests.

While a bot runs, the room keeps a single status line (current step, elapsed time, collapsed earlier steps). Prompt reading, tool calls and interim narration arrive as `step` events; only the concluding answer is a normal message. See the stack README, section "Work status".

Room messages and files, each bot's memory, and a bot's VM working data (browser profile, cookies, room and Downloads) are separate. The sidebar lists what each area holds and deletes only that area. Message, file, and memory deletes can be undone for a few seconds; resetting a bot's working data cannot. `POST /api/reset` remains the immediate full room purge. See the stack README, section "Room, memory, and bot working data".

The transcript follows the stream only while it is pinned to the bottom. An upward wheel, touch, key, or scrollbar drag lets go immediately; scrolling back to within about 40 pixels, the down arrow, or sending a message pins again. See the stack README, section "Smart autoscroll".

Reactions sit on the message (one row per actor and emoji, toggle to remove). Hover or a long-press opens the bar; pills show the count and update live. Every bot has `react` and `unreact`. A reaction from a person is context on that bot's next turn and does not start a run unless `MAG_ROOM_REACT_WAKE=1`. See the stack README, section "Emoji reactions".

The room installs as an app named MAG. Web Push is opt-in (VAPID keys from the environment, never from git). The shell and `sw.js` are not cached; versioned static files are. A newer build reloads when the composer is idle, or shows “Neue Version – aktualisieren” while a message is being typed or streamed. See the stack README, section "Installable app, Web Push, and updates". `notify(PushNote(...))` in `native/push.hpp` is the hook a finished Job should call.

Build/deploy once from the repository root through `CMakeLists.txt` and `ops/`.
There is no separate Python server or alternate room installer.

## Long coding tasks / large files

`run_command` transports commands through a private stdin file and the guest
agent in 24 KiB chunks: no 8,000-character or Linux argv limit (max 32 MiB
per call). Full shell output stays in a private
`/home/bot/.mag-shell/*.log` inside that bot's VM; the tool result includes
the path, byte count and a 32 KiB preview. No output is silently discarded.

Use `write_file(path, content)` or `append_file(path, content)` for source
files under `/home/bot` rather than heredocs. Writes use an atomic temporary
sibling, fsync and rename. Append copies the old data before replacement.
Maximum 32 MiB per payload; split more across append calls.

A pending `Nächster Schritt` / `Next step` or `[[MAG:WEITER]]` is
not completion: the agent continues using tools in the same turn even with
autotalk off. Defaults: 96 steps and four hours (configurable via
`MAG_ROOM_MAX_STEPS`, `MAG_ROOM_RUN_BUDGET_S`). On exhaustion the answer
reports **incomplete**, preserving existing VM files.

Deploy `magroom`, `magbot-native` and the guest wrapper only. **Never restart
`mag-llm`** for this change.


## Browser-Spracherkennung – Deutsch (Moonshine WASM)

Eine Runtime und das **Small Streaming German** Modell auf Desktop, iPhone Safari und Android; keine Server-Inferenz, kein CDN/Caddy, keine Änderungen an `mag-llm`. Mikrofon, Inferenz-Worker und Modell laden erst nach Klick. Zwischenergebnisse erscheinen direkt im Composer und endgültige Ergebnisse bleiben vor dem Senden editierbar.

**Vorbereitung:** `bash ops/vendor-speech-assets.sh build/speech-assets`. Stellt das gepinnte Moonshine-WASM-Paket und acht Modell-Dateien bereit, überprüft die Byte-Längen und legt sie unter inhaltsgehashten Namen in `build/speech-assets/` ab. Die binären Modell-Dateien (~122 MB) werden **nicht in Git** versioniert, sondern bei Build/Deploy auf dem Host selbst gehostet. `ops/deploy.sh` kopiert sie noch vor dem atomaren Releasewechsel in `web/static/`. Ohne Modellpaket kein erfolgreicher Deploy; für Offline-Releases den geprüften Snapshot vorher behalten.

**nginx:** Der bestehende private `mag-room.nginx.conf` bindet optional `/opt/mag-stack/current/ops/nginx/mag-speech*.inc` ein. Nach Release `nginx -t`, dann nginx reload. `auth_request` wird von der Server-Ebene geerbt; keine Freigabe für nicht authentifizierte Clients. MIME `application/wasm`, COOP/COEP/CORP und `speech-config.json` ohne Cache sind erforderlich. Gehashte Runtime/WASM-Dateien werden cache-first gespeichert, große Modellgewichte nur über den Moonshine-Cache (nicht doppelt im Service Worker).

**Tests:** nach CMake-Build `python3 -m unittest discover -s mag-room/tests -p 'test_speech.py' -v`; nach Asset-Vendoring auch `test_speech_real.py`. Physisches iPhone/Android für deutsche Diktierqualität und Speicherverbrauch zusätzlich testen.

### Context safety (Q4 / IQ3)
Both resident models currently have a 32768-token context. MAG builds a bounded
room transcript (default 16 KB, newest messages first), and limits tool results
carried to later model steps to 6 KB each. It includes the tool-schema size in
an overall 52 KB wire-byte estimate and keeps a compact action handoff when
dropping older complete assistant/tool rounds. An upstream
`exceed_context_size_error` causes up to two smaller-context retries **of the
same model step**, without re-executing completed tools. All room messages and
files remain persisted unchanged. Controls:
`MAG_ROOM_CONTEXT_BYTES`, `MAG_ROOM_TRANSCRIPT_BYTES`,
`MAG_ROOM_TOOL_CONTEXT_BYTES` (see `.env.example`). Context compaction is a
conservative byte heuristic, not exact model tokenization; watch prompt usage
when adding MCP servers with many schemas.

### Single-model operation (opt-in)
To reduce model switching and route all room bots to the stable Q4 server,
`MAG_ROOM_SINGLE_MODEL=off` is the default. This applies even when a bot or the
room previously selected IQ3: the persisted preferences are not overwritten.
The UI offers both models by default; only Q4 while the opt-in switch is enabled. To restore the previous dual-model routing
without restoring files or migrating bot state, set `MAG_ROOM_SINGLE_MODEL=off`
and restart **only** `mag-120` via the protected room deployment path.
**This setting does not unload, restart or stop the IQ3 llama service:** freeing
its CPU/RAM is a separate host operation requiring an idle-task and live-health
check. This also does not change Q4 context or generation speed.
