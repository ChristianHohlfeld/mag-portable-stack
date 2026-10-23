# Per-NUMA model hot-swap

Each NUMA node serves exactly **one** model from `catalog.json`; at most two
models are loaded at any time. Nothing swaps automatically: the owner picks a
node and a model in the room, confirms the dialog, and the room runs
`sudo -n /usr/local/lib/mag-numa/mag-numa-swap swap <node> <model>`.

## Flow (`mag-room/native/mag_numa_swap.cpp`)

1. `flock` on the state file: one swap at a time (exit 5 otherwise).
2. Idempotent: if the node already serves the model and `/health` is 200, exit 0.
3. Admission: `ram_gib + reserve_gib <= node budget_gib` and
   `ram_gib + model on the other node <= host.limit_gib`. Refused = exit 3 with a
   readable reason, nothing is stopped.
4. Unload first: stop and disable every unit that can hold a model on that node
   (`mag-numaN.service` + `legacy_units`, e.g. `mag-llm.service`). If more than one
   model is still loaded afterwards the swap aborts (never a third model).
5. Write the generated unit, `daemon-reload`, start, poll `/health`; progress =
   cgroup `memory.current` / model size.
6. Success: enable the unit (survives reboot), state `serving`. Failure: stop it,
   restore the previous unit, start and health-check it again (exit 6), or report
   `error` if that also fails (exit 7).

State: `/var/lib/mag-numa/state.json` (read by the room and by `mag-audit-gateway`,
which routes a request for a model to the node that serves it).

## Generated unit safeguards

`numactl --physcpubind=<node cpus> --membind=<node>`, `CPUAffinity`/`AllowedCPUs`,
`AllowedMemoryNodes`, `NUMAPolicy=bind`, `NUMAMask`; `MemoryMin=MemoryLow=ram`,
`MemoryHigh=ram+8G`, `MemoryMax=ram+16G`, `MemorySwapMax=0`; `--mlock` (or the
runtime's `--load-mode mlock`) with `LimitMEMLOCK=infinity`; `OOMScoreAdjust=-900`.
Check with `mag-numa-swap render <node> <model>`.

## Install (blank host: `ops/bootstrap.sh` phase `numa`)

`sudo bash ops/numa/install.sh` installs the binary, a sudoers rule allowing the
room user only `mag-numa-swap swap *`, seeds the state from the units running now
and writes `/etc/mag-stack/numa.env`. It does not touch any model. Restart the
room to enable the selector. Before the first swap, measure each model's RssAnon
and adjust `ram_gib`, node `budget_gib` and `host.limit_gib`; the Qwen binary path
expects a `current` symlink to the pinned `ops/qwen/runtime.sha` build.

Tests: `python3 ops/numa/tests/test_numa_swap.py` (fake systemctl/health),
room behaviour in `mag-room/tests/test_agent_core.py` (`NumaRoom`).
