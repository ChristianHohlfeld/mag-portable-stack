# Directional state driver

The runtime follows a direction, not a mandatory exact final state. For explicit owner tasks the existing proof and consent contracts remain authoritative.

## Feedback loop

1. **Orient:** optional aim, current orientation, and owner-task revision.
2. **Observe:** verified file path/hash, actual command exit/output hash, or semantic browser snapshot. An unverified action is recorded as such.
3. **Compare:** maintain current facts, observation history, novelty, and revisits. A new invocation is not proof of improvement. Re-indexed DOM elements alone are not new state.
4. **Adapt:** use the model's judgment of direction against actual evidence. The controller detects stagnation and cycles; it does not invent a universal numerical distance or claim task completion.
5. **Act:** the next native tool call is chosen by the model. No specific task, command or strategy is hard-coded.
6. **Reorient/yield:** allow repeated observations; request a different approach at half tolerance. On sustained lack of novelty, yield the work phase without closing the goal. Persist facts and strategy revision.
7. **Continue:** autonomous dialogue resumes through the existing scheduler. An explicitly commissioned browser task may resume after bounded exponential cooldown, first observing the actual browser, never replaying a previously executed action automatically.

The controller is deterministic given its evidence inputs. Its state is persisted in the bot wire metadata or owner task record; bounded history (128 observations) and facts (64 keys) prevent unbounded metadata growth. History is not a full world model. Observations can become stale; new evidence can supersede them. Novelty is a proxy for information, not semantic proximity to a target.

## Safety and scheduling

- Native tool permissions, own-VM scope, explicit Stop, pause, owner priority and external action approval remain in force.
- Automatic task recovery applies only to still-active owner tasks after recoverable decision/observation failures; login walls and explicit blockers do not trigger automatic retries.
- Auto-Talk is independent of task recovery and is not disabled by it. Active owner tasks still have scheduler priority.
- No artificial inference timeout for autonomous CPU conversation or active browser goals. Existing per-tool execution limits still protect host resources.
- No automatic completion from novelty, state hashes or shell exit status. Explicit user-task completion still needs the existing verified proof.
- MAG_ROOM_RECOVERY_TOLERANCE: 8 by default (4–32 base, adaptive expansion up to 64).
- MAG_ROOM_GOAL_RETRY_BASE_S: 30 seconds by default; repeated unproductive cycles increase delay up to 300 seconds (or the explicitly configured larger base).
