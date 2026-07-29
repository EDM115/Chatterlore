# Plan 002: Make Markdown a privacy-correct projection of committed memory
> **Executor instructions**: Follow this plan step by step, run every verification command, and stop on any STOP condition. When complete, update this plan's row in plans/README.md unless a reviewer owns the index.
> **Drift check (run first)**: <code>git diff --stat beda837..HEAD -- src/pzp_ai_bot/memory/markdown.py src/pzp_ai_bot/memory/service.py src/pzp_ai_bot/app_context.py tests/test_memory_quality.py tests/test_memory_service.py tests/test_operations.py docs/privacy.md docs/architecture.md docs/operations.md PLAN.md</code>. Reconcile any changed lifecycle excerpt before editing.
## Status
- **Implementation status**: DONE (2026-07-29)
- **Audit finding**: 2
- **Priority**: P1
- **Effort**: L
- **Risk**: HIGH
- **Depends on**: none
- **Category**: security
- **Planned at**: commit beda837, 2026-07-13
## Why this matters
SQLite is documented as operational truth and Markdown as readable durable memory, but the daily Markdown files are append-only side effects written before transaction commit. Review proposals enter Markdown before approval, and rejection, retention, item deletion, or forget cannot remove old blocks. The durable files must instead be reproducible projections of committed KnowledgeItem rows so privacy operations apply to every content-bearing store.
## Current state
- src/pzp_ai_bot/memory/markdown.py:25-48 appends content to daily/YYYY-MM-DD/<scope>.md and has no deletion or rebuild operation.
- src/pzp_ai_bot/memory/service.py:1807-1819 writes review_proposal content before approval.
- src/pzp_ai_bot/memory/service.py:1696-1738 deletes messages, knowledge items, and embeddings, then rebuilds only canonical MEMORY.md.
- docs/privacy.md:9-11 promises summaries/manual notes rather than raw dumps, approval before durable memory, and deletion of raw messages, durable knowledge, and embeddings.
- tests/test_memory_quality.py:58-106 is the review workflow exemplar; tests/test_memory_service.py and tests/test_operations.py contain confirmed forget flows.
Problem excerpts:
    markdown.py:46-47
    with path.open("a", encoding="utf-8") as handle:
        handle.write(block)
    service.py:1807-1813
    if scope_settings.review_before_compact:
        review = await MemoryReviewRepository(session).add(...)
        self.markdown.append_daily(..., kind="review_proposal")
    service.py:1727-1736
    messages = await MessageRepository(session).delete_scope(scope)
    items = await KnowledgeRepository(session).delete_scope(scope)
    embeddings = await EmbeddingRepository(session).delete_scope(scope)
    ...
    await self.compact_memory()
## Commands you will need
| Purpose | Command | Expected on success |
|---|---|---|
| Lifecycle tests | <code>.\.venv\Scripts\python.exe -m pytest -q tests/test_memory_quality.py tests/test_memory_service.py tests/test_operations.py</code> | all tests pass |
| Format check | <code>.\.venv\Scripts\ruff.exe format --check .</code> | exit 0 |
| Lint | <code>.\.venv\Scripts\ruff.exe check .</code> | exit 0 |
| Types | <code>.\.venv\Scripts\ty.exe check</code> | exit 0 |
| Full tests | <code>.\.venv\Scripts\python.exe -m pytest -q</code> | all offline tests pass |
Use the existing .venv, not system Python. Do not run a destructive cleanup command against a computed directory; all projection cleanup must resolve and validate paths beneath Settings.memory_dir.
## Scope
**In scope**:
- src/pzp_ai_bot/memory/markdown.py
- src/pzp_ai_bot/memory/service.py
- src/pzp_ai_bot/app_context.py
- tests/test_memory_quality.py
- tests/test_memory_service.py
- tests/test_operations.py
- docs/privacy.md
- docs/architecture.md
- docs/operations.md
- PLAN.md
**Out of scope**:
- Deleting bot_events or AIUsage audit metadata
- Changing Scope.key, Scope.filename_stem, confirmation-token behavior, or the daily path convention
- Incremental canonical-compaction optimization; plan 012 owns that
- A new external storage system or a second source of truth
- Git staging, commits, branches, pushes, resets, or worktrees
## Git workflow
- Use read-only git status/diff only; the user owns all additive git actions.
## Steps
### Step 1: Define SQLite-authoritative projection APIs
Replace append-only semantics with deterministic MarkdownMemoryStore methods that receive committed KnowledgeItem rows and rebuild daily files from row.created_at, row.kind, row.source_message_ids, Scope.filename_stem, and row.content. Add a safe full daily rebuild that writes generated files under a sibling staging directory, validates every resolved target remains inside memory_dir, replaces generated files, and removes obsolete generated scope files without touching unrelated paths. Keep MEMORY.md generation separate.
**Verify**: add a store-level test using tmp_path that rebuilds twice and proves byte-identical output with no duplicate block.
### Step 2: Remove proposals and transaction-time writes
Delete the review_proposal append. Pending MemoryReviewItem content must remain in SQLite only. Remove Markdown writes from _add_knowledge_in_session and every other in-transaction mutation path; no filesystem content write may occur before session_scope commits.
**Verify**: <code>rg -n "append_daily|review_proposal" src/pzp_ai_bot</code> → no production call writes review proposals or performs an append from an active database transaction.
### Step 3: Rebuild the daily projection after committed mutations
Add a MemoryService projection method that opens a fresh read session after the business transaction commits, loads committed KnowledgeItem rows, and invokes the deterministic store rebuild. Call it after remember/import/summary approval/note edit or delete/item delete/retention/forget paths that can alter durable knowledge. Keep EventRepository audit records for each admin-visible operation and add a content-free markdown_projection_failed event through a fresh session if a post-commit file rebuild fails.
**Verify**: lifecycle tests prove successful mutation writes exactly the committed items and a simulated business-transaction rollback writes nothing.
### Step 4: Reconcile pre-existing stale daily files on startup
After build_context has upgraded schema and constructed MemoryService, invoke a projection reconciliation that rebuilds daily files from committed KnowledgeItem rows without contacting an AI provider. This must remove historical review proposals and deleted items on the next safe startup. It must not compact through a provider or modify audit rows.
**Verify**: create a stale daily file in a temp context, seed only one committed item, rebuild context, and assert the stale content is absent while the committed item remains.
### Step 5: Characterize every privacy lifecycle
Add tests for rejected review, approved review, note edit/delete, item delete, scoped forget, global forget across at least two scopes, raw and knowledge retention, and a forced database rollback. Assert SQLite state, daily files, canonical MEMORY.md, and audit counts separately. For global forget, assert every generated daily scope file containing memory is removed while bot_events remain.
**Verify**: <code>.\.venv\Scripts\python.exe -m pytest -q tests/test_memory_quality.py tests/test_memory_service.py tests/test_operations.py</code> → all cases pass.
### Step 6: Update canonical documentation and roadmap
Document SQLite as source of truth, daily Markdown as a rebuildable projection, pending reviews as SQLite-only, startup reconciliation, and the exact forget/audit boundary. Update PLAN.md DONE/PARTIAL entries, test map, risks, version/date, and changelog.
**Verify**: <code>rg -n "projection|pending review|forget|audit" docs/privacy.md docs/architecture.md docs/operations.md PLAN.md</code> → the lifecycle is explicit and consistent.
### Step 7: Run all quality gates
Run the format, lint, type, targeted, and full test commands.
**Verify**: all commands exit 0 and only in-scope implementation/docs/tests plus plan status files are modified.
## Test plan
- Use the context fixture so SQLite and memory_dir are isolated and providers remain fake/offline.
- Inspect actual daily files and MEMORY.md; database-only assertions cannot prove privacy deletion.
- Seed two concrete scopes for global forget and one review proposal that is rejected.
- Force an exception before commit and assert no new Markdown appears.
- Seed stale historical daily content before context construction and prove startup reconciliation removes it.
## Done criteria
- [x] No review proposal is written to Markdown before approval.
- [x] No content-bearing Markdown write occurs inside a business transaction.
- [x] Daily Markdown can be deterministically rebuilt from committed KnowledgeItem rows.
- [x] Rejection, deletion, retention, scoped forget, and global forget remove all affected Markdown content.
- [x] Existing stale generated files are reconciled safely on startup without provider calls.
- [x] bot_events remain as documented metadata and contain no memory content.
- [x] Privacy/architecture/operations docs and PLAN.md match behavior.
- [x] Targeted and full gates pass.

Verification on 2026-07-29 used `uv run`: the complete lifecycle target across `tests/test_memory_quality.py`, `tests/test_memory_service.py` and `tests/test_operations.py` passed, Ruff format and lint passed, `ty check` passed, the full offline pytest suite passed, and the production source contains no `append_daily` or `review_proposal` references.
## STOP conditions
- Daily Markdown contains information not reconstructible from KnowledgeItem rows and no documented source of truth exists.
- Safe reconciliation would require deleting files outside the resolved Settings.memory_dir daily tree.
- A proposed approach recursively deletes a computed path without explicit containment validation.
- Rebuilding daily files would contact an external provider.
- A content write still occurs before the corresponding database commit.
## Maintenance notes
- Future durable-memory mutations must update SQLite first and project Markdown only after commit.
- Reviewers must test filesystem state, not only database counts.
- Plan 012 may later make canonical compaction scope-incremental, but it must preserve this source-of-truth boundary.
