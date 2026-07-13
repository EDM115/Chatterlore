# Plan 004: Keep background loops alive and make health reflect their state
> **Executor instructions**: Follow every step, run every gate, and update plans/README.md when done unless a reviewer owns it.
> **Drift check (run first)**: <code>git diff --stat beda837..HEAD -- src/pzp_ai_bot/telegram/scheduler.py src/pzp_ai_bot/telegram/webhook.py src/pzp_ai_bot/telegram/polling.py src/pzp_ai_bot/telegram/safe_api.py tests/test_operations.py tests/test_webhook.py tests/test_telegram_integration.py docs/operations.md docs/deployment.md PLAN.md</code>.
## Status
- **Audit finding**: 4
- **Priority**: P1
- **Effort**: M
- **Risk**: MED
- **Depends on**: none
- **Category**: bug
- **Planned at**: commit beda837, 2026-07-13
## Why this matters
The idle summarizer and runtime-settings refresher are started once and contain no exception boundary. A transient database, provider, or settings failure therefore terminates a loop for the rest of the process, while webhook /health continues returning ok. The runtime needs cancellation-safe supervision, bounded backoff, structured diagnostics, and a health response that fails when a critical task is no longer running.
## Current state
- scheduler.py:9-23 awaits summarize_idle and refresh_runtime_settings directly inside infinite loops.
- scheduler.py:26-33 creates each asyncio task once.
- webhook.py:41-55 owns task startup/shutdown, but webhook.py:73-75 always returns {"ok": True}.
- tests/test_operations.py:143-164 only verifies the configured refresher sleep interval.
- telegram/safe_api.py is the local structlog warning exemplar; match its event-style structured logging and never log secrets or message content.
Current loop:
    while True:
        await asyncio.sleep(...)
        await context.memory.summarize_idle()
Current health:
    @app.get("/health")
    async def health() -> dict[str, bool]:
        return {"ok": True}
## Commands you will need
| Purpose | Command | Expected on success |
|---|---|---|
| Scheduler/webhook tests | <code>.\.venv\Scripts\python.exe -m pytest -q tests/test_operations.py tests/test_webhook.py tests/test_telegram_integration.py</code> | all pass |
| Format | <code>.\.venv\Scripts\ruff.exe format --check .</code> | exit 0 |
| Lint | <code>.\.venv\Scripts\ruff.exe check .</code> | exit 0 |
| Types | <code>.\.venv\Scripts\ty.exe check</code> | exit 0 |
| Full tests | <code>.\.venv\Scripts\python.exe -m pytest -q</code> | all offline tests pass |
## Scope
**In scope**:
- src/pzp_ai_bot/telegram/scheduler.py
- src/pzp_ai_bot/telegram/webhook.py
- src/pzp_ai_bot/telegram/polling.py only for shared lifecycle wiring
- tests/test_operations.py
- tests/test_webhook.py
- tests/test_telegram_integration.py
- docs/operations.md
- docs/deployment.md
- PLAN.md
**Out of scope**:
- Adding a new monitoring vendor, queue system, or external heartbeat
- Treating asyncio.CancelledError as a recoverable task failure
- Logging prompts, messages, tokens, provider keys, or webhook secrets
- Changing summary cadence or runtime setting semantics
- Git mutations
## Git workflow
- Use read-only git status/diff only; no stage, commit, push, branch, reset, or worktree.
## Steps
### Step 1: Add explicit task health state
Define a small typed state object per background loop containing task name, running flag, last success timestamp, last failure timestamp, last exception type, and consecutive failures. Keep it process-local and content-free. Return task handle plus health state from startup helpers or use a BackgroundTaskHandle dataclass.
**Verify**: ty check passes and unit tests can inspect state without sleeping in real time.
### Step 2: Make loops cancellation-safe and resilient
Wrap each iteration, not the entire loop, in an Exception boundary. Re-raise asyncio.CancelledError immediately. On success reset failure count and record last_success_at. On failure record metadata, emit one structured warning, and sleep with deterministic bounded exponential backoff capped by the normal interval or a documented maximum. Then continue.
**Verify**: a test double that fails once and succeeds once produces two calls, one warning state, and a recovered healthy state.
### Step 3: Harden shutdown
Make stop_task safe for running, already-cancelled, and unexpectedly completed tasks. Shutdown must not hide an existing task exception, but it also must not prevent the other task or owned Telegram session from closing. Emit content-free structured diagnostics for unexpected completion.
**Verify**: tests cover cancellation and an already-failed task without leaking an unhandled-task warning.
### Step 4: Wire webhook health to critical task state
Store both task handles/states on FastAPI app.state during lifespan. Return HTTP 200 only when both critical loops are running and not done; return HTTP 503 with booleans and task names when either loop is dead. Do not expose exception messages. Keep the existing webhook authentication path unchanged.
**Verify**: <code>.\.venv\Scripts\python.exe -m pytest -q tests/test_webhook.py -k health</code> → healthy and failed-task responses pass.
### Step 5: Preserve polling lifecycle
Use the same supervised task helpers in polling. Add dispatcher-startup/shutdown coverage proving both tasks start, one task failure is recoverable, and both are stopped during shutdown.
**Verify**: <code>.\.venv\Scripts\python.exe -m pytest -q tests/test_telegram_integration.py -k polling</code> → selected tests pass.
### Step 6: Update runbooks and PLAN
Document health semantics, task names, recovery/backoff, what operators should do after repeated failure, and which fields are safe to log. Update PLAN.md with tests, operational status, risks, date/version, and changelog.
**Verify**: <code>rg -n "503|idle summarizer|settings refresher|backoff|health" docs/operations.md docs/deployment.md PLAN.md</code> → operator behavior is documented.
### Step 7: Run all gates
Run all command-table checks.
**Verify**: every command exits 0 and only in-scope files are modified.
## Test plan
- Use monkeypatched asyncio.sleep; never add real delays.
- Cover fail-once/recover, repeated failures/backoff cap, cancellation during sleep, cancellation during work, already-dead task shutdown, health 200, and health 503.
- Assert logged/returned diagnostics contain task and exception type but not raw exception text or secrets.
## Done criteria
- [ ] One ordinary Exception cannot permanently kill either loop.
- [ ] CancelledError exits promptly.
- [ ] Backoff is bounded and deterministic under tests.
- [ ] Webhook health returns 503 when a critical task is dead.
- [ ] Shutdown handles all task states and closes owned resources.
- [ ] Operations/deployment docs and PLAN.md match behavior.
- [ ] Targeted and full gates pass.
## STOP conditions
- The host/runtime already provides a different supervisor that would result in nested restart loops.
- Reliable health requires a new external dependency.
- A proposed diagnostic would expose raw messages, prompts, secrets, or arbitrary exception strings.
- Tests require real time sleeps or network access.
## Maintenance notes
- Add every future critical background task to the same health registry and lifecycle.
- Reviewers should distinguish recoverable iteration failure from fatal programmer/configuration errors and ensure cancellation is never swallowed.
