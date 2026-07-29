# Plan 012: Canonicalize only dirty scopes and reuse committed snapshots
> **Executor instructions**: Execute only after the privacy projection and transaction-boundary plans. Follow every gate and update plans/README.md when complete unless a reviewer owns it.
> **Drift check (run first)**: <code>git diff --stat beda837..HEAD -- src/pzp_ai_bot/db/models.py src/pzp_ai_bot/db/repositories.py src/pzp_ai_bot/memory/service.py src/pzp_ai_bot/memory/canonical.py src/pzp_ai_bot/memory/markdown.py alembic/versions tests/test_memory_quality.py tests/test_product_controls.py docs/architecture.md docs/operations.md PLAN.md</code>.
## Status
- **Status**: DONE
- **Audit finding**: 10
- **Priority**: P2
- **Effort**: L
- **Risk**: HIGH
- **Depends on**: plans/002-rebuild-markdown-from-committed-memory.md, plans/006-gate-alembic-model-drift.md, plans/008-bound-and-claim-ai-work.md, plans/010-persist-provider-failure-telemetry.md
- **Category**: perf
- **Planned at**: commit beda837, 2026-07-13
## Why this matters
Creating, editing, or deleting one note currently loads every KnowledgeItem, calls the chat provider once for every scope, and rewrites canonical MEMORY.md. Cost and latency grow with the entire installation. Persisted per-scope canonical snapshots can reuse unchanged provider output while SQLite remains authoritative and a deterministic final render combines every current snapshot.
## Current state
- service.py:828, 900, and 916 call compact_memory after one-scope note mutations.
- service.py:1030-1083 loads all knowledge, iterates every grouped scope, calls _canonical_entries_for_scope, writes all canonical output, and records usage inside one session.
- canonical.py:41-54 includes every scope item in each provider prompt.
- tests/test_memory_quality.py:27-55 is the canonical grouping/dedup/source-link exemplar.
Current global path:
    items = await knowledge_repo.list_scope()
    for scope, scope_items in grouped_items.items():
        entries, result, error, ... = await self._canonical_entries_for_scope(session, scope, scope_items)
    path = self.markdown.write_canonical(entries_by_scope, metadata_by_scope, sources_by_scope)
## Commands you will need
| Purpose | Command | Expected on success |
|---|---|---|
| Compaction tests | <code>.\.venv\Scripts\python.exe -m pytest -q tests/test_memory_quality.py tests/test_product_controls.py</code> | all pass |
| Migration | <code>.\.venv\Scripts\alembic.exe upgrade head; .\.venv\Scripts\alembic.exe check</code> | upgrade passes; no drift |
| Format/lint/type | <code>.\.venv\Scripts\ruff.exe format --check .; .\.venv\Scripts\ruff.exe check .; .\.venv\Scripts\ty.exe check</code> | each exits 0 |
| Full tests | <code>.\.venv\Scripts\python.exe -m pytest -q</code> | all offline tests pass |
## Scope
**In scope**:
- src/pzp_ai_bot/db/models.py
- src/pzp_ai_bot/db/repositories.py
- src/pzp_ai_bot/memory/service.py
- src/pzp_ai_bot/memory/canonical.py
- src/pzp_ai_bot/memory/markdown.py
- one new Alembic revision
- tests/test_memory_quality.py
- tests/test_product_controls.py
- docs/architecture.md
- docs/operations.md
- PLAN.md
**Out of scope**:
- Changing canonical categories, source-marker format, Scope keys, or provider selection
- Replacing SQLite/Markdown
- Reintroducing content-bearing Markdown as source of truth
- Git mutations
## Git workflow
- Read-only git inspection only; no stage, commit, push, branch, reset, or worktree.
## Steps
### Step 1: Persist per-scope canonical snapshots
Add a CanonicalScopeSnapshot model and migration keyed by stable Scope.key, with chat_id/thread_id, item_fingerprint, entries_json, provider/profile/model/reasoning/prompt-version metadata, and updated_at. The fingerprint must deterministically include item ID, kind, content hash, source IDs, updated_at, canonical prompt version, and all provider/prompt settings that affect output. Do not store raw messages.
**Verify**: fresh/upgrade migrations and alembic check pass; Scope.key uniquely identifies main/topic/global cases without nullable uniqueness.
### Step 2: Add snapshot repository operations
Implement get/list/upsert/delete_scope/delete_missing operations through repository classes and session_scope. Validate entries_json into CanonicalMemoryEntry values at the boundary. Corrupt snapshot data must fail closed and trigger recomputation for that scope, not silently disappear from MEMORY.md.
**Verify**: repository tests cover round trip, replacement, deleted scope, and corrupt payload recovery.
### Step 3: Refactor compaction into read, provider, commit, render phases
Change compact_memory to accept dirty scopes and a force flag. Read committed items/settings and calculate fingerprints in a short session; reuse matching snapshots; call providers only for missing/dirty fingerprints outside transactions per plan 008; reopen a short session to verify fingerprints still match current items before upsert. If items changed during generation, discard/retry that scope rather than committing stale output.
**Verify**: a blocked provider plus concurrent note edit cannot save a snapshot for the old fingerprint.
### Step 4: Pass affected scopes from every mutation
Update remember/import/summary approval/note edit-delete/item delete/retention/forget paths to pass their concrete affected scopes. Global forget removes all snapshots. Deleting the last item in a scope removes that snapshot. Retain an explicit force-full rebuild callable for repair/testing, but routine mutations must never default to every scope.
**Verify**: a recorder with two scopes observes one provider call when only scope A changes and zero calls for unchanged scope B.
### Step 5: Render canonical MEMORY.md from current snapshots
Load all current snapshots, current metadata, and batched source references, then write MEMORY.md deterministically. A scope with committed knowledge but no valid snapshot must be computed before rendering; a scope with no committed knowledge must not survive from stale cache. Preserve category/source-link output exactly.
**Verify**: existing canonical tests pass byte/semantic assertions and deletion removes stale scope output.
### Step 6: Add cost/correctness regressions
Cover one-scope create/edit/delete, two-scope isolation, no-op compaction, changed provider/prompt fingerprint, full force rebuild, concurrent drift discard, failed provider retaining the last valid snapshot plus dirty status, and last-item deletion. Assert provider call counts and AIUsage operation counts.
**Verify**: targeted compaction tests pass deterministically with fake providers.
### Step 7: Document cache authority and update PLAN
Document snapshots as derived cache, SQLite KnowledgeItem as authority, fingerprint inputs, repair/full rebuild semantics, and failure behavior. Update PLAN.md migration head, performance tests, operational runbook, risks, version/date, and changelog.
**Verify**: <code>rg -n "canonical snapshot|fingerprint|dirty scope|full rebuild" docs/architecture.md docs/operations.md PLAN.md</code> → behavior is explicit.
### Step 8: Run all gates
Run migrations, targeted tests, format, lint, ty, and full tests.
**Verify**: all exit 0 and only in-scope files are modified.
## Test plan
- Unchanged scope triggers zero provider calls.
- One dirty scope among multiple triggers one call.
- Item/provider/prompt changes invalidate fingerprint.
- Concurrent changes cannot commit stale snapshots.
- Provider failure preserves last valid canonical output and marks scope dirty/retryable.
- Deletion removes snapshots and MEMORY.md content.
- Force rebuild recomputes all non-empty scopes.
## Done criteria
- [x] Routine mutations canonicalize only affected scopes.
- [x] Unchanged provider output is reused from validated derived snapshots.
- [x] Snapshot fingerprints cover every output-affecting input.
- [x] Concurrent drift and failed providers cannot publish stale/empty canonical memory.
- [x] SQLite remains authoritative and full repair is possible.
- [x] Migration/docs/PLAN and all gates pass.
## STOP conditions
- Plan 002 or 008 is incomplete, leaving filesystem writes or provider calls inside business transactions.
- Canonical output depends on an input not represented in the fingerprint.
- Snapshot corruption would cause silent data loss instead of recomputation.
- A correct implementation requires treating cached Markdown as authoritative.
## Maintenance notes
- Any future prompt/provider/item metadata that affects canonical output must join the fingerprint.
- Reviewers should focus on stale-cache deletion and concurrent mutation behavior, not only provider call counts.
