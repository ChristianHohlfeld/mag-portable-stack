# Code contracts and testing

## Protocol and tools
The frontend sends messages to the same C++ application, which emits SSE events and coordinates one model request at a time. `jobs_list`, `jobs_get`, `jobs_create`, `jobs_update`, `jobs_delete`, `jobs_pause`, `jobs_resume`, `jobs_run_now` and `jobs_history` call the native jobs service; UI and MCP have identical schedules/validation/queue behavior. Bot changes mark `created_by=bot`; creation is idempotent by key. A bot must not receive webhook secrets; deletion permissions depend on job ownership.

DeepSeek DSML tool calls are normalized to the expected OpenAI-format tool-calling contracts with stable schema ordering. Unparseable calls are ignored instead of retried; one valid call is executed per step. A running response is cancelled through `POST /api/cancel` without discarding queued user messages or restarting the model. The partial assistant response is retained as interrupted.

## Permissions
Backend `identify_actor` trusts forwarded Tailnet identities **only for connections from the local nginx proxy**. nginx first deletes client-supplied `X-Tailscale-*` headers, obtains fresh identity via magroom C++ LocalAPI auth endpoint, then writes the trusted values. Guest restrictions must be enforced server-side independently of hidden frontend controls. `/_mag_whois` is internal and exempt from its parent `auth_request` to prevent recursion.

## Verification
`cmake -S . -B build -G Ninja -DCMAKE_BUILD_TYPE=Release && cmake --build build -j 4`; then `python3 mag-room/tests/test_native.py`. `test_17b` finds an actual non-loopback address using UDP route/source selection, local interface enumeration and hostname fallback; it bypasses CI HTTP proxies. If no non-loopback interface exists, the test explicitly skips instead of attempting Docker-specific addresses.

`python3 mag-room/tests/test_private_nginx.py` starts a **real local test nginx** with generated throwaway TLS, mock native-auth endpoint and mock backend. It must prove whois-denial never reaches backend, spoofed client headers are overwritten and shell/API routes have `no-store`. Tests run in CI and do not touch production.
