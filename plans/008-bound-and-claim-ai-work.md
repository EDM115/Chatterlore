# Plan 008: Bound AI batches, claim summary work atomically, and shorten database transactions
> **Executor instructions**: This is a high-risk architecture correction. Follow the order exactly, run each gate, and stop rather than improvising. Update plans/README.md when complete unless a reviewer owns it.
> **Drift check (run first)**: <code>git diff --stat beda837..HEAD -- src/pzp_ai_bot/db/models.py src/pzp_ai_bot/db/repositories.py src/pzp_ai_bot/db/session.py src/pzp_ai_bot/memory/service.py alembic/versions tests/test_memory_correctness.py tests/test_memory_quality.py tests/test_operations.py docs/architecture.md docs/operations.md docs/privacy.md PLAN.md</code>.
## Status
- **Audit finding**: 5
- **Priority**: P1
- **Effort**: L
- **Risk**: HIGH
- **Depends on**: plans/002-rebuild-markdown-from-committed-memory.md, plans/003-keep-provider-failures-retryable.md, plans/006-gate-alembic-model-drift.md, plans/007-enforce-concurrent-message-idempotency.md
- **Category**: tech-debt
- **Planned at**: commit beda837, 2026-07-13
## Why this matters
summary_batch_size currently triggers summarization but does not cap it: every pending message enters one provider prompt. Pending rows are not claimed, so concurrent manual, idle, and ingest-triggered summaries can process the same sources. Provider calls also occur while session_scope retains writes, extending SQLite locks for network latency. The pipeline needs bounded claims, provider calls outside transactions, short idempotent finalization, stale-claim recovery, and unchanged public scope semantics.
## Current state
- repositories.py:177-189 already accepts a limit, but service.py:751 and service.py:1748 call pending_for_summary without one.
- service.py:1865-1868 appends every pending message to one prompt.
- service.py:684-759 inserts/flushed a message, awaits embedding and possibly chat generation, then commits only when session_scope exits.
- service.py:1740-1787 selects pending rows and marks them summarized without a claim token.
- session.py:67-74 is the mandatory commit/rollback boundary; AGENTS.md forbids manual commits scattered through service methods.
Current trigger:
    pending = await message_repo.pending_for_summary(incoming.scope)
    if len(pending) >= scope_settings.summary_batch_size:
        await self._summarize_pending_in_session(session, incoming.scope, pending, scope_settings)
## Commands you will need
| Purpose | Command | Expected on success |
|---|---|---|
| Pipeline tests | <code>.\.venv\Scripts\python.exe -m pytest -q tests/test_memory_correctness.py tests/test_memory_quality.py tests/test_operations.py</code> | all pass |
| Migration | <code>.\.venv\Scripts\alembic.exe upgrade head; .\.venv\Scripts\alembic.exe check</code> | upgrade exits 0; no drift |
| Format/lint/type | <code>.\.venv\Scripts\ruff.exe format --check .; .\.venv\Scripts\ruff.exe check .; .\.venv\Scripts\ty.exe check</code> | each exits 0 |
| Full tests | <code>.\.venv\Scripts\python.exe -m pytest -q</code> | all offline tests pass |
## Scope
**In scope**:
- src/pzp_ai_bot/db/models.py
- src/pzp_ai_bot/db/repositories.py
- src/pzp_ai_bot/memory/service.py
- one new Alembic revision
- tests/test_memory_correctness.py
- tests/test_memory_quality.py
- tests/test_operations.py
- docs/architecture.md
- docs/operations.md
- docs/privacy.md
- PLAN.md
**Out of scope**:
- A distributed queue, multi-node worker fleet, or new database backend
- Changing the configured summary_batch_size meaning
- Bulk import/export streaming; plan 019 owns it
- Dirty-scope canonical caching; plan 012 owns it
- Manual commits inside service methods
- Git mutations
## Git workflow
- Read-only git inspection only; no stage, commit, push, branch, reset, or worktree.
## Steps
### Step 1: Add persisted summary claims
Create the next revision after the live head and add nullable summary_claim_token and summary_claimed_at columns to messages plus an index supporting learned=true, summarized_at NULL, scope, and claim age. Reflect them in Message. Define a typed SummaryClaim value containing a random token, concrete Scope, ordered message IDs, and source Telegram IDs.
**Verify**: fresh upgrade, upgrade from the previous head, and alembic check pass.
### Step 2: Implement atomic bounded claim/release/finalize repository operations
Add MessageRepository.claim_for_summary(scope, limit, token, now, stale_before) using one SQLite update guarded by summarized_at NULL and unclaimed-or-stale criteria, followed by selection by token. Preserve created_at/id order and never claim more than limit. Add release_summary_claim(token) and finalize_summary_claim(token, summarized_at) compare-and-set operations. A stale claim becomes eligible only after a documented timeout longer than expected provider latency.
**Verify**: repository tests prove two concurrent claimers receive disjoint message sets and every set length is at most summary_batch_size.
### Step 3: Move provider calls outside session_scope
Refactor orchestration into three phases: short read/claim transaction; provider call with no live AsyncSession; short finalization transaction. Convert embedding helpers into provider-only calls that return vectors and usage metadata without accepting a session. For ingest/remember/refresh/import call sites, gather immutable inputs first, perform provider work outside a transaction, then persist result in a fresh session_scope. Preserve current failure atomicity: if embedding fails before persistence, do not write partial durable memory. Keep all commits owned by session_scope.
**Verify**: a test blocks a fake provider call while a second session successfully writes an unrelated policy or audit row before the provider is released.
### Step 4: Finalize claimed summaries idempotently
After a successful provider response, open a short transaction, verify every source is still owned by the token and unsummarized, create exactly one KnowledgeItem or MemoryReviewItem, mark only those rows summarized, clear the claim, and add scoped AI/audit metadata. If ownership changed, discard the result and record a content-free stale_result event; do not create duplicate knowledge. On provider failure, release the claim and leave rows pending as established by plan 003.
**Verify**: concurrent summarize_now and idle summarization produce one durable result per claimed batch and no duplicate source group.
### Step 5: Process backlogs in bounded loops
Change ingest-triggered, summarize_now, and idle paths to claim/process repeated batches of at most effective summary_batch_size. Define command semantics explicitly: summarize_now processes until no eligible rows remain; ingest processes at most one batch to keep update latency bounded; idle processing uses a bounded number of batches per tick so one scope cannot starve others. Count only successfully finalized messages.
**Verify**: a backlog larger than three batches results in provider requests whose source count never exceeds the setting and whose union covers each message once.
### Step 6: Recover stale claims
On each claim attempt, allow claims older than the timeout to be reclaimed. Add a startup-safe or scheduler path that reports the number of stale claims recovered through content-free audit metadata. Never mark stale rows summarized merely because a worker disappeared.
**Verify**: a test seeds an old claim, advances a controlled clock, and proves it is processed once; a fresh claim remains unavailable.
### Step 7: Document the state machine and update PLAN
In docs/architecture.md document pending → claimed → summarized/review-pending, claim timeout, transaction boundaries, and per-runtime batch limits. In docs/operations.md document stale claim observability/recovery. In docs/privacy.md state that failed claims remain pending and never become raw durable content. Update PLAN.md migration head, tests, runbook, risks, version/date, and changelog.
**Verify**: <code>rg -n "claim|batch|transaction|stale" docs/architecture.md docs/operations.md docs/privacy.md PLAN.md</code> → state machine and operator behavior are explicit.
### Step 8: Run all gates
Run migration, targeted, format, lint, ty, and full tests.
**Verify**: all exit 0 and only in-scope files are modified.
## Test plan
- No provider request receives more than summary_batch_size messages.
- Concurrent claimers receive disjoint sets.
- Ingest handles at most one summary batch; manual/idle semantics match documented limits.
- Provider failure releases a claim and creates no durable content.
- Stale claims recover; fresh claims do not.
- Finalization is compare-and-set idempotent.
- A blocked provider does not hold a SQLite write lock.
- Review mode and direct-knowledge mode both preserve source IDs.
## Done criteria
- [ ] summary_batch_size is a hard per-request cap.
- [ ] Pending work is atomically claimed and finalization is idempotent.
- [ ] Provider calls run without a live write transaction.
- [ ] Failures and stale workers leave work retryable.
- [ ] Ingest, manual, idle, review, and direct-summary paths share the same state machine.
- [ ] Migration and Alembic drift checks pass.
- [ ] Architecture/privacy/operations docs and PLAN are current.
- [ ] Targeted and full gates pass.
## STOP conditions
- The supported SQLite version lacks the atomic update/returning behavior needed for claims.
- Moving provider calls exposes a new partial-write behavior not covered by the stated atomicity rule.
- A caller depends on holding an ORM object/session across provider I/O.
- Correctness requires a distributed lease/queue beyond the single-node v1 constraint.
- Any change introduces manual commits inside service methods.
## Maintenance notes
- Any new AI operation must follow read/claim → provider outside transaction → short idempotent write.
- Reviewers should test concurrency with independent sessions, not only sequential mocks.
- Plans 010, 012, and 019 build on this transaction boundary; do not execute them first.
