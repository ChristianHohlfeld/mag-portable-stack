# MAG agent architecture (Grok-Bot-style turn model)

MAG implements a comparable agent workflow, not proprietary Grok Bot software.
Unlike Grok Bot's shared account computer, each MAG bot retains a separate
restricted VM. Explicit delegation passes a task and outcome through the room;
it never shares browser cookies, credentials or filesystem privileges.

How a MAG room bot pursues a goal to completion. Everything is enforced in
the C++ room (`mag-room/native/magroom.cpp`, `agent_core.hpp`, `numa_models.hpp`);
the model only proposes text and tool calls.

## What an assistant like Grok Bot / Ali does

1. **Immediate acknowledgement**: the user sees within a second that the
   request arrived and who owns it.
2. **Work in steps**: model call → tool calls → real tool results → next model
   call, until the goal is verified. Every step is visible (collapsed).
3. **Milestones**: short progress lines when a sub-goal is done, never a wall
   of text and never silence.
4. **Verified final result**: the final message says what was done and how it
   was checked (tool evidence), or exactly what blocks it.
5. **Goal tracking**: an explicit todo list; the turn does not end while items
   are open unless the bot marks them blocked.
6. **Background work**: long sub-goals run as sub-agents while the assistant
   stays responsive; each has status and a real cancel.
7. **Durable memory**: a profile (facts) and a dated journal, with recall.
8. **Routines**: scheduled prompts that run without the user.
9. **User preempts**: a new user message is read at the next step boundary;
   Stop ends everything at once.

## How MAG maps it

| Concern | Mechanism |
|---|---|
| Turn owner | A user message selects exactly one owner (first selected bot). Bot messages are context only and never schedule work. |
| Ack | `run_begin` publishes the run card immediately (`phase=ack`, "Verstanden"), before the prompt is evaluated. |
| Step loop | `run_turn`: up to `MAG_ROOM_MAX_STEPS` (96) model calls / `MAG_ROOM_RUN_BUDGET_S` (4 h). Tool results are real (`dispatch_tool`); identical failures stop blind retries. |
| Never stop early | A pending `[[MAG:WEITER]]`/"Nächster Schritt" or **open todo items** inject a system reminder instead of ending; three identical stalls end the turn with a diagnosis. |
| Milestones | `todo` tool (`set/add/done/block`). Every change is published as a `todo` event and shown in the run card ("✓ item"); interim narration becomes collapsed steps. |
| Final result | Only the concluding text is a room message; rules require stating verification. Budget exhaustion reports **incomplete**. |
| Sub-agents and bot handoff | `task(start/delegate/status/cancel)` on target bot model/VM/tools, own approvals, depth 1, max `MAG_ROOM_MAX_TASKS` (2) per bot. Delegator can inspect/cancel. Each task runs on an independent lane and yields to human work. The room saves the task registry (full prompt, owner, attempts, status and result) in `STATE/tasks.json` **before** executing and on completion. On crash/redeploy, in-flight tasks become `interrupted` instead of automatically replaying external side effects. `GET /api/tasks`, `POST /api/tasks/<id>/cancel`, owner-confirmed `POST /api/tasks/<id>/resume`. Resume preserves task ID/todos and first instructs the agent to verify external state, with no repeat of non-idempotent actions without new approval. |
| Memory | Profile = `remember` facts (agents.json). Journal = `STATE/journal/<bot>.jsonl`. `memory` tool: `recall` (search profile+journal), `journal`, `update`, `forget`. UI: `GET/PUT/DELETE /api/agents/<id>/memory...`. |
| Routines | Jobs (`jobs.hpp`): schedules (minutes … monthly, once) and webhook events, pause/resume, retries, cancel via Stop. Bots use the routines MCP tool server (`jobs_*` tools in the room, `POST /mcp/jobs` for external MCP clients). **A bot only proposes**: `jobs_create`/`jobs_update`/`jobs_resume` show a confirmation card (name, schedule/trigger, prompt); only "Speichern" stores it. "Ablehnen" is final: the same proposal (idempotency key or name) is refused without a new card (`STATE/routine-rejections.json`). Runs post progress and result into the room as the bot; nothing is sent outside. Every job belongs to exactly one bot (missing owners are bound on load to `created_by_bot`, otherwise the first bot). The full prompt stays out of the static system prefix; a short skill catalog for that bot only is appended with the memories. The left list selects a bot; the right side shows its active switch, browser, console and jobs (create, edit, pause, delete). |
| Secrets | `secret` tool: `request` shows a masked input card; the value goes to `POST /api/secrets/requests/<id>` → AES-256-GCM in `STATE/secrets.json` with the key from the systemd credential `mag-secrets-key` (`ops/secrets/install.sh`, in the encrypted backup, restored by bootstrap). The bot only gets the handle `{{secret:NAME}}`, expanded inside `browser_type`/`run_command` arguments; every tool result and error is scrubbed (`[secret:NAME]`) before model, room or log. Owner API: list (name, purpose, bots, last used — never values), replace, set bot access, delete. |
| Preemption | Between steps `take_user_input` hands a new user message to the running owner ("has priority"); input for another bot makes the run yield. |
| Stop | `POST /api/stop` cancels every lane, every sub-agent task and closes backend sockets (llama stops decoding). |
| Two bots | Lanes per model/NUMA node: two bots on two nodes work in parallel; the user can always type. Room rules from PR #105 apply. |
| Models | `numa_models.hpp`: each NUMA node serves one catalog model; bots are assigned to nodes; explicit, confirmed hot-swap with rollback (see `ops/numa/README.md`). Bots on a swapping node queue. |

## Invariants (tested in `mag-room/tests/test_native.py`)

- Idle stays idle: no tool success + no `[[MAG:WEITER]]` → no continuation.
- One owner per user request; bounded continuation (`AUTO_MAX_TURNS`).
- Stop cancels main turns *and* tasks; cancelled tasks report `cancelled`.
- Task registry survives process restart; a restarted task is held until explicit owner recovery, never silently re-executed.
- Open todos keep the turn going (bounded), done/blocked todos let it end.
- Background task final state is `incomplete` (never `done`) when any todo remains open, the answer is empty, or the model truncates/skips; the room explicitly labels this outcome `unvollständig`. This is a minimal deterministic guard, **not** a universal proof of external goal completion.
- Never more than two models are loaded; swaps are never automatic.
- Routine proposals need confirmation; a rejection is final.
- A secret value never appears in model requests, room state, logs or
  plain-text state files.

## Live action highlight (browser view)

Before `browser_click`/`browser_type` the room asks mag-browser `/locate`
for the target's viewport box (one CDP eval, scroll into view, no DOM
injection) and publishes it with the existing `browser_action` events
(`browser_events.hpp`). The client (`static/live-highlight.js`) draws a
fading outline, the agent cursor, click ripple, typed text at the field and
scroll hints over the stream, mapped with the stream's object-position so it
stays aligned under zoom/pan. Password/secret fields are sent as dots only
(`masked`), never the value. No model tokens; toggle ◎ in the view bar
(default on). Actions of a bot whose browser is not shown appear as a caption.

## Bootstrap context

`mag-room/native/bot_context.md` is the single, versioned bootstrap context
(host, stack services, tools/rights, owner, German-default language rule).
CMake embeds it at build time (`build/generated/bot_context.hpp`). The room
appends it to every bot's stable system prompt (`build_system`, cacheable
prefix), so new and reset bots always get it. `magbot-native` writes a copy to
`/home/bot/MAG_CONTEXT.md` at VM creation, and "Bot zurücksetzen"
(`/api/agents/<id>/workspace/reset`) writes it again. Edit the markdown only,
keep it short, and never put secrets, IPs or private details in it.
Covered by `BootstrapContext` in `mag-room/tests/test_native.py`.
