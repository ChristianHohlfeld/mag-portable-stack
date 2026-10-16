# Native MAG Jobs MCP

Implementation: `jobs.hpp` (authoritative service, persistence, scheduler), `jobs_mcp.hpp` (MCP protocol and adapter), `jobs_tools.hpp` (nine immutable OpenAI schemas), `routes.hpp` (shared UI/MCP routes), `magroom.cpp` (model registration and safe execution). There is **one** store and scheduler; no job service in the browser, VM, Python, or MCP process.

## Paths and principals

* The UI continues to call `/api/jobs`, `/api/jobs/{id}`, `/run`, `/pause`, `/secret`. These now delegate to `jobs_request()`.
* Model tools `jobs_list`, `jobs_get`, `jobs_create`, `jobs_update`, `jobs_delete`, `jobs_pause`, `jobs_resume`, `jobs_run_now`, `jobs_history` invoke `jobs_request()` with a trusted bot identity read from the server's agent registry. Bot identity is not accepted as a tool parameter.
* `POST /mcp/jobs` is an optional authenticated native MCP JSON-RPC endpoint. It is disabled unless `MAG_JOBS_MCP_TOKEN` is set to a secret of at least 32 characters. Remote MCP callers holding that token act as the **human user**, not as a bot. Never give this token to the model, a VM, or an unauthenticated client.
* Bot-created jobs persist `created_by="bot"`, `created_by_bot=<id>`, and `idempotency_key`; existing jobs migrate as `created_by="user"`. The UI and bot tools get the same non-secret job representation.
* Bot deletion is permitted only for `created_by=bot` with a matching `created_by_bot`. For other jobs the model gets a clear error; the user must approve and delete using the **existing UI confirmation**. The model cannot forge approval using tool arguments. Webhook secrets are never returned to bot callers.
* The browser's VM bridge explicitly blocks the jobs API and MCP endpoint. Do not expose `/mcp/jobs` over an unauthenticated reverse proxy. Only the real MAG host possesses the MCP token.

## Calling through DeepSeek

All nine tools are registered at the same position in the function list on every request, including chat-only bots. They are kept even during the final model round. Keep `--jinja` and use the DeepSeek V4 Flash template matching the GGUF (DSML `<｜DSML｜tool_calls>` conversion is the inference server's job). Process **at most one** well-formed call per model round; silently ignore invalid JSON object arguments, instead of passing an empty object or retrying malformed output. Keep the returned assistant `tool_calls` and matching `tool_call_id` for the following model request.

Example of a model-side create call:

```json
{"name":"jobs_create","arguments":{"name":"Morgenbrief","prompt":"Fasse meine Projektänderungen kurz zusammen","schedule":{"kind":"weekdays","time":"08:00"},"idempotency_key":"morgenbrief-start-20261016"}}
```

`schedule.kind` is one of `every_minutes`, `every_hours`, `every_days`, `every_weeks`, `daily`, `weekdays`, `weekly`, `monthly`, `once`, `webhook`. Required fields: `every` (1..10000) for intervals; `time` (HH:MM, Berlin) for daily/weekdays/weekly/monthly; `weekday` (0=Sunday) for weekly; `day` (1..31) for monthly; `at` (`YYYY-MM-DDTHH:MM`, Europe/Berlin) for once. `webhook` creates a secret that is shown **only once to a human UI creator**, not a bot. The internal native scheduler remains `schedule.type` based; conversion happens in the adapter.

A recurring hourly job:

```json
{"name":"jobs_create","arguments":{"name":"Check","prompt":"Prüfe das Projekt","schedule":{"kind":"every_hours","every":1},"idempotency_key":"hourly-check-v1"}}
```

## Remote MCP requests

Set `MAG_JOBS_MCP_TOKEN` securely, for example via `openssl rand -hex 32` in your root-only service environment. Restart MAG without restarting/reloading the LLM model. The following illustrative request uses the legacy-compatible MCP JSON-RPC form (the endpoint also accepts the modern result shape when 2026-07-28 metadata is supplied):

```bash
curl -sS https://mag.tail3e02ae.ts.net:8446/mcp/jobs \
  -H "Authorization: Bearer $MAG_JOBS_MCP_TOKEN" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

For `tools/call`, send `{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"jobs_list","arguments":{}}}`. For modern 2026-07-28 requests also include `MCP-Protocol-Version`, `Mcp-Method`, and `Mcp-Name` (where required), plus the spec's `_meta` identity/version/capability fields.

## Checks

```bash
cmake -S . -B build -G Ninja -DCMAKE_BUILD_TYPE=Release
cmake --build build -j 4
python3 mag-room/tests/test_native.py
```

Regression tests cover the MCP tool list and ordering, human MCP create -> identical UI GET/list records, bot create -> persistent bot ownership and idempotency, bot deletion of own jobs, rejection of foreign-job deletion, and a malformed tool call that must not execute or loop. The production model and user VMs are not exercised in CI.

## Boundaries and deployment

The current MAG UI has an explicit delete confirmation. That confirmation is the only supported human approval route for foreign-job deletion. Do not add a model-provided `confirmed=true` flag. The optional remote endpoint uses bearer authentication rather than the UI's origin check alone. Set a production secret only if remote MCP is needed; local DeepSeek bots already work with no MCP HTTP listener access. `MAG_TZ` must remain `Europe/Berlin`; the once-time grammar is deliberately local ISO date-time **without an offset**, since the entire room uses that named timezone. DST-invalid times are rejected; ambiguous fall-back times use the C runtime's local choice. No promises are made about live deployment without a host-level integration check.
