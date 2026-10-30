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

### MAG fast path: one coding bot, two hot model choices

MAG runs **one active existing bot**, with its VM, git, browser, files and
memories. `MAG_ROOM_SINGLE_BOT=auto` chooses a stored bot (prefers an existing
VM); other stored bots stay on disk but never run. Adding a second bot is
disabled. The regular deploy removes the old multi-bot systemd override.

Q4 runs on NUMA0 at port 8090, IQ3 on NUMA1 at port 8091. Both are resident
independently and have their own RAM prompt caches. The Q4/IQ3 selector writes
`room-model.txt`; the *same bot* routes its **next** request through the
central audit gateway to exactly the selected backend. Neither process is
restarted, unloaded or swapped on selection. There is **no fallback**: an
unknown model fails with HTTP 400, a stopped selected backend with HTTP 502.
This is deliberate, to avoid silently running on another model.

Normal room deployments never restart an LLM. No CPU-speed benefit from
routing alone is claimed; CPU performance remains limited by available
Xeon/AVX2 compute, model size and token-cache misses. If IQ3 is not running,
use the protected MAG host process to verify NUMA1 memory and start only IQ3,
without touching Q4. See `docs/AI_PERFORMANCE_RECOVERY.md`.


### GLM-5.3-Flash: compact host-directed chat (PR #238)
Only for `bot_model_id` starting with `glm-5.3-flash` outside an explicit job/task, reflection or self-talk mode, the host uses a compact, timestamp-free `GLM_CHAT_RULES` string and bot identity. It does **not** include old `bot_context.md`, `ROOM_RULES`, `AGENT_RULES`, VM/WEB/FILES/FRONTEND policies, Pho instruction, tool inventories or clock. The separate bounded `[Erinnerungen]` block contains up to 10 existing facts, at most 150 UTF-8 bytes (hard byte-level upper bound; model-specific tokenization still requires post-deploy verification). No `tools` or `tool_choice` keys are transmitted in GLM chat, including its prewarm anchor; no chat tool follow-up is executed. GLM chat defaults to `MAG_ROOM_GLM_CHAT_MAX_TOKENS=256`. Explicit `think=1` limits the thinking turn to 256 tokens and permits exactly one final non-thinking attempt when the model runs out of thinking budget.

The host—not the model—selects bot recipients (explicit UI selection, name or @name, both/alle, otherwise one configured `MAG_ROOM_DEFAULT_BOT_ID` or first enabled bot). Paused bots are excluded. Bot output never recursively schedules another GLM chat reply. Only the owner can send `/merk <Fakt>` or `/vergiss <n>` to manage an existing bot's memories. `/skill <name>` or an exact description trigger selects at most one `ops/room/skills/<name>/SKILL.md`; valid YAML front matter has `name` and `description`, with a ≤300-UTF-8-byte body (stricter than the 300-token requirement). Only the body is appended to the most recent user message; skills do not alter the system prefix or grant tools.

The GLM prefix-only slot snapshot uses a new `-chat-v2-prefix.bin` filename, preserving old checkpoint files, with a single warmup per changed prefix and a diagnostic `glm-chat prefix tokens=N` **estimate** (UTF-8 byte count divided by 3, not a tokenizer proof). The actual token budget and cache-hit rate need verification on MAG; the native test suite uses a fake backend, never a real GLM or benchmark. Existing C7 model process identity checks and the expanded file comparisons fail closed if an installed GLM runtime differs; neither model server nor its unit flags are changed.

**Documented upstream deviation:** Z.ai's managed GLM-5.3-Flash uses **forced thinking**, and does not advertise thinking-off mode. Here, the *local patched llama.cpp GLM template* receives `enable_thinking=false`, `clear_thinking=true` so its empty `<think></think>` differs intentionally from Z.ai API behavior, because CPU decoding was measured around **2.9 tokens/s**. `reasoning_effort="low"` remains byte-stable for all local GLM requests; only the explicit per-message thinking switch changes the suffix. This is a deployment-specific adaptation, not an official GLM recommendation.

Sources (critical comparison): [Anthropic Context Engineering](https://www.anthropic.com/research/effective-context-engineering-for-ai-agents), [Anthropic Building effective agents](https://www.anthropic.com/research/building-effective-agents), [Anthropic Agent Skills overview](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview), [OpenAI practical guide to agents](https://openai.com/business/guides-and-resources/a-practical-guide-to-building-ai-agents/), [OpenAI Prompt Caching](https://platform.openai.com/docs/guides/prompt-caching), [llama.cpp server README](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md), [Z.ai Thinking Mode](https://docs.z.ai/guides/capabilities/thinking-mode), [Hugging Face GLM-5.3-Flash](https://huggingface.co/zai-org/GLM-5.3-Flash), [arXiv 2603.11409: Speak or Stay Silent](https://arxiv.org/abs/2603.11409), [arXiv 2501.17258: Controlling AI Agent Participation](https://arxiv.org/abs/2501.17258). Unlike Anthropic's metadata-first Skills discovery, GLM chat never injects a skill catalog into the prompt; unlike learned group turn-taking, bot selection is deterministic for reliability.
