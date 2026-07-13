# Plan 018: Normalize telemetry scope, aggregate in SQL, and index audit queries
> **Executor instructions**: Execute after durable failure telemetry is in place. Follow migrations and tests exactly, then update plans/README.md unless a reviewer owns it.
> **Drift check (run first)**: <code>git diff --stat beda837..HEAD -- src/pzp_ai_bot/db/models.py src/pzp_ai_bot/db/repositories.py src/pzp_ai_bot/memory/service.py alembic/versions tests/test_product_controls.py tests/test_operations.py tests/test_cli.py docs/architecture.md docs/operations.md PLAN.md</code>.
## Status
- **Audit finding**: lower-priority telemetry aggregation/index opportunity
- **Priority**: P3
- **Effort**: M
- **Risk**: MED
- **Depends on**: plans/006-gate-alembic-model-drift.md, plans/010-persist-provider-failure-telemetry.md
- **Category**: perf
- **Planned at**: commit beda837, 2026-07-13
## Why this matters
AI usage status currently materializes the complete ai_usage table, filters exact Scope.key strings in Python, and aggregates row by row. That is unbounded and also fails chat-scope hierarchy semantics because a chat query does not include topic rows. bot_events and ai_usage are append-only and lack indexes for their main scope/type/time access paths. Normalize scope columns, backfill existing rows, aggregate in SQL, and index both tables.
## Current state
- models.py:317-340 defines BotEvent and AIUsage without query indexes; AIUsage has no chat/thread columns.
- repositories.py:1496-1516 selects every AIUsage row and compares metadata_json["scope"] == scope.key.
- EventRepository.list_events filters event_type/scope and sorts by created_at.
- Scope hierarchy requires global contains all, chat contains its topics, and topic contains only itself through scope_filter/Scope.contains.
Current aggregation:
    rows = list((await self.session.scalars(select(AIUsage))).all())
    if scope is not None:
        rows = [row for row in rows if row.metadata_json.get("scope") == scope.key]
    for row in rows:
        ...
## Commands you will need
| Purpose | Command | Expected on success |
|---|---|---|
| Telemetry tests | <code>.\.venv\Scripts\python.exe -m pytest -q tests/test_product_controls.py tests/test_operations.py tests/test_cli.py</code> | all pass |
| Migration | <code>.\.venv\Scripts\alembic.exe upgrade head; .\.venv\Scripts\alembic.exe check</code> | upgrade passes; no drift |
| Format/lint/type | <code>.\.venv\Scripts\ruff.exe format --check .; .\.venv\Scripts\ruff.exe check .; .\.venv\Scripts\ty.exe check</code> | each exits 0 |
| Full tests | <code>.\.venv\Scripts\python.exe -m pytest -q</code> | all offline tests pass |
## Scope
**In scope**:
- src/pzp_ai_bot/db/models.py
- src/pzp_ai_bot/db/repositories.py
- src/pzp_ai_bot/memory/service.py
- one new Alembic revision
- tests/test_product_controls.py
- tests/test_operations.py
- tests/test_cli.py
- docs/architecture.md
- docs/operations.md
- PLAN.md
**Out of scope**:
- Deleting or retaining telemetry rows
- A metrics vendor/dashboard
- Storing prompts, raw messages, or secrets in normalized columns
- Changing user-facing status field names
- Git mutations
## Git workflow
- Read-only git inspection only; no stage, commit, push, branch, reset, or worktree.
## Steps
### Step 1: Add normalized AIUsage scope columns and indexes
Add nullable chat_id and message_thread_id to AIUsage. Add indexes for ai_usage scope/created_at, operation/created_at, and error/created_at as justified by live queries. Add BotEvent indexes for event_type/scope/created_at and scope/created_at. Use one Alembic revision and reflect all indexes in metadata.
**Verify**: fresh migration, upgrade, and alembic check pass.
### Step 2: Backfill existing AIUsage rows
Read metadata_json.scope for existing rows and parse only supported Scope forms. Backfill concrete chat/topic columns; global/unscoped rows remain NULL. Invalid legacy values remain unscoped and are counted globally, with a migration summary or documented behavior rather than an exception that bricks upgrade. Never copy content fields.
**Verify**: migration test seeds chat, topic, all, missing, and invalid metadata values and verifies deterministic columns.
### Step 3: Make scope explicit on writes
Change EventRepository.add_ai_usage to accept Scope | None and populate columns directly. Update every provider call site to pass the same Scope represented in metadata. Keep metadata_json scope only if useful for backward-compatible exports, but stop using it as the query key.
**Verify**: new usage rows have normalized columns for message, summary, query, answer, and compaction operations.
### Step 4: Aggregate entirely in SQL
Rewrite ai_usage_stats with COUNT, conditional error count, AVG latency, and grouped operation counts. Apply scope_filter(AIUsage, scope) so chat includes topics and topic is exact. Do not materialize individual AIUsage rows.
**Verify**: a query-count/statement test confirms aggregate SELECTs only; chat status equals main plus topic rows while topic status remains isolated.
### Step 5: Validate event indexes and query plans
Use SQLite EXPLAIN QUERY PLAN in a focused test or manual verification to confirm representative event and usage queries use the new indexes. Avoid brittle assertions on full text; assert the expected index name appears.
**Verify**: index-plan checks pass on seeded rows.
### Step 6: Document and update PLAN
Document normalized metadata, hierarchy, indexes, and SQL aggregation in architecture/operations docs. Update PLAN.md migration head, telemetry tests, performance notes, version/date/changelog.
**Verify**: <code>rg -n "AIUsage|aggregate|index|scope hierarchy" docs/architecture.md docs/operations.md PLAN.md</code> → behavior is explicit.
### Step 7: Run all gates
Run migration, targeted, format, lint, ty, and full tests.
**Verify**: all exit 0 and only in-scope files are modified.
## Test plan
- Migration backfills supported legacy scope values.
- Chat aggregation includes main and topics; topic aggregation is exact; global includes all.
- Counts/errors/average/operations match seeded expected values.
- New writes populate normalized columns.
- EXPLAIN uses intended indexes.
- User-facing status format remains stable.
## Done criteria
- [ ] No ai_usage_stats path loads all usage rows.
- [ ] Scope hierarchy is correct.
- [ ] New and legacy telemetry is queryable through normalized columns.
- [ ] BotEvent and AIUsage access paths are indexed.
- [ ] Migration/docs/PLAN and all gates pass.
## STOP conditions
- Existing metadata contains an undocumented scope format that cannot be mapped safely.
- Index additions materially degrade the primary write workload in a measured test.
- A proposed backfill copies prompt/message content into new columns.
## Maintenance notes
- New telemetry dimensions that become query keys should be normalized deliberately rather than buried only in JSON.
