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
