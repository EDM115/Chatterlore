# Plan 007: Enforce logical message uniqueness under concurrent SQLite writes
> **Executor instructions**: Follow every step and gate, then update plans/README.md unless a reviewer owns it.
> **Drift check (run first)**: <code>git diff --stat beda837..HEAD -- src/pzp_ai_bot/db/models.py src/pzp_ai_bot/db/repositories.py alembic/versions tests/test_memory_correctness.py tests/test_cli.py docs/architecture.md PLAN.md</code>.
## Status
- **Status**: DONE
- **Audit finding**: 6
- **Priority**: P1
- **Effort**: M
- **Risk**: MED
- **Depends on**: plans/006-gate-alembic-model-drift.md
- **Category**: migration
- **Planned at**: commit beda837, 2026-07-13
## Why this matters
Telegram may redeliver updates concurrently. The current SELECT-then-INSERT repository path is race-prone, and SQLite permits multiple rows through a unique constraint when message_thread_id is NULL. Main-chat duplicate deliveries can therefore create duplicate Message and Embedding rows despite the sequential idempotency test.
## Current state
- models.py:28-32 declares UniqueConstraint(chat_id, message_thread_id, telegram_message_id), while message_thread_id is nullable at line 38.
- repositories.py:144-174 selects, then constructs and flushes a Message in a separate operation.
- tests/test_memory_correctness.py:69-88 covers only sequential duplicate ingestion.
- PLAN.md:RISK-002 already recognizes nullable SQLite logical uniqueness.
Current shape:
    existing = await self.session.scalar(select(Message).where(...))
    if existing is not None:
        return existing, False
    self.session.add(message)
    await self.session.flush()
## Commands you will need
| Purpose | Command | Expected on success |
|---|---|---|
| Correctness/migration tests | <code>uv run pytest -q tests/test_memory_correctness.py tests/test_cli.py</code> | all pass |
| Migrate disposable DB | <code>uv run alembic upgrade head</code> | exit 0 |
| Drift check | <code>uv run alembic check</code> | no new operations |
| Format/lint/type | <code>uv run ruff format --check .; uv run ruff check .; uv run ty check</code> | each exits 0 |
| Full tests | <code>uv run pytest -q --cov=pzp_ai_bot --cov-branch --cov-report=term-missing</code> | all offline tests pass and coverage stays above the floor |
Use only a disposable DB_URL for migration verification.
## Implementation result
Live head 0007 required the new revision number 0008. Revision `0008_message_logical_uniqueness` keeps the lowest duplicate message ID, retains one embedding per provider/model, repoints survivors, deletes duplicate messages and creates the authoritative unique expression index over `chat_id`, `COALESCE(message_thread_id, -1)` and `telegram_message_id`; the older nullable constraint remains to avoid a needless table rebuild. Repository insertion now uses SQLite `ON CONFLICT DO NOTHING ... RETURNING` and fetches only on conflict. Five repeated two-session main-chat races each produced one created row and one shared ID, while different chats/topics remain distinct. Twenty focused tests, fresh upgrade, clean Alembic check, Ruff format/lint, `ty` and the complete coverage suite passed on 2026-07-29 with 187 passed, 1 skipped and 81.08% coverage. SQLite's expression-index reflection warning is covered by a direct `sqlite_master` assertion.
## Scope
**In scope**:
- src/pzp_ai_bot/db/models.py
- src/pzp_ai_bot/db/repositories.py
- one new Alembic revision under alembic/versions
- tests/test_memory_correctness.py
- tests/test_cli.py
- docs/architecture.md
- PLAN.md
**Out of scope**:
- Replacing NULL main-thread semantics or changing Scope.key/filename behavior
- Deduplicating policies or unrelated tables
- General summary claiming; plan 008 owns that
- Supporting a new database backend
- Git mutations
## Git workflow
- Read-only git inspection only; the user owns stage/commit/push/branch/worktree actions.
## Steps
### Step 1: Add a SQLite logical-scope unique index
Create the next Alembic revision after the live head. If the head remains 0006, name it 0007_message_logical_uniqueness. Add a unique expression index over chat_id, COALESCE(message_thread_id, a sentinel outside valid Telegram topic IDs), and telegram_message_id. Reflect the same index in SQLAlchemy metadata without changing message_thread_id NULL semantics. Keep the old unique constraint if dropping it would require an unnecessary table rebuild; document why the expression index is authoritative.
**Verify**: alembic upgrade head and alembic check both pass on a new disposable database.
### Step 2: Reconcile pre-existing duplicates before index creation
Within the migration, group duplicate logical messages, keep the lowest Message.id, and reconcile Embedding rows that target duplicate message IDs. Delete an embedding when the keeper already has the same provider/model embedding; otherwise repoint it to the keeper. Then delete duplicate Message rows before creating the index. Do not alter Telegram source_message_ids stored in knowledge items because those are Telegram IDs, not Message.id values.
**Verify**: migration test upgrades to the previous revision, seeds two main-chat duplicates plus overlapping/non-overlapping embeddings, upgrades to head, and finds one message with one embedding per provider/model.
### Step 3: Replace SELECT-then-INSERT with an atomic insert-or-fetch
Implement MessageRepository.add with SQLite INSERT ... ON CONFLICT DO NOTHING and RETURNING the inserted ID. When no row is returned, select the logical existing row with the current NULL-aware predicate and return created=False. Preserve the current tuple contract and do not catch IntegrityError by rolling back the caller's entire session.
**Verify**: sequential duplicate test still reports duplicate_message and one message/embedding.
### Step 4: Add deterministic concurrent tests
Use two independent sessions against the same temp SQLite database and a barrier/event so both operations contend on the same main-chat logical key. Assert both calls complete, exactly one reports created=True, both resolve the same Message.id, and the database contains one row. Add a topic-scope case to prove legitimate different topic IDs remain distinct.
**Verify**: run the concurrency test repeatedly in one pytest invocation without flakes.
### Step 5: Update architecture and roadmap
Document the logical key and SQLite expression-index choice in docs/architecture.md. Update PLAN.md correctness task, migration head, tests, risks, date/version, and changelog.
**Verify**: <code>rg -n "logical.*message|COALESCE|concurrent|0007" docs/architecture.md PLAN.md</code> → the invariant is explicit.
### Step 6: Run all gates
Run targeted tests, migration gates, format, lint, ty, and full tests.
**Verify**: all exit 0 and only in-scope files are modified.
## Test plan
- Sequential main-chat duplicate remains idempotent.
- Two concurrent main-chat inserts collapse to one row.
- Same Telegram message ID in different chats is allowed.
- Same Telegram message ID in two different topics of one chat is allowed.
- Pre-upgrade duplicates and embeddings are reconciled deterministically.
- Fresh schema and upgrade-from-previous-head both pass.
## Done criteria
- [x] Database uniqueness treats NULL main thread as one logical value.
- [x] Repository insertion is atomic and returns the existing row on conflict.
- [x] Existing duplicate data is reconciled without orphan embeddings.
- [x] Concurrent and migration regressions pass.
- [x] Alembic check is clean.
- [x] Architecture docs and PLAN match the invariant.
- [x] Full gates pass.
## STOP conditions
- Telegram can legitimately use the chosen sentinel as a topic ID.
- The live Alembic head is no longer the revision assumed here; renumber after reconciliation rather than creating a branch.
- Duplicate rows contain conflicting non-derivable content that cannot be merged by the specified keeper rule.
- SQLite in the supported runtime lacks the needed expression-index or RETURNING support.
## Maintenance notes
- Every future nullable logical unique key should use a database-enforced canonical representation, not only an application pre-check.
- Reviewers should inspect migration behavior on existing duplicate embeddings, not just a fresh database.
