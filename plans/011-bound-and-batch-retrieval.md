# Plan 011: Batch retrieval hydration and enforce a measured candidate bound
> **Executor instructions**: Follow every step and gate. This plan deliberately preserves the SQLite brute-force backend while bounding its worst case. Update plans/README.md when complete unless a reviewer owns it.
> **Drift check (run first)**: <code>git diff --stat beda837..HEAD -- src/pzp_ai_bot/memory/retrieval.py src/pzp_ai_bot/memory/vector.py src/pzp_ai_bot/memory/service.py src/pzp_ai_bot/db/repositories.py src/pzp_ai_bot/config.py tests/test_memory_correctness.py tests/test_memory_tools.py tests/test_operations.py docs/architecture.md docs/operations.md PLAN.md</code>.
## Status
- **Status**: DONE
- **Audit finding**: 9
- **Priority**: P2
- **Effort**: L
- **Risk**: MED
- **Depends on**: none
- **Category**: perf
- **Planned at**: commit beda837, 2026-07-13
## Why this matters
Each search currently loads and decodes every embedding in each source scope, awaits a separate database lookup for every candidate, normalizes the same query vector repeatedly, and sorts complete lists. Cross-scope profiles multiply the work. The first safe improvement is batched hydration and single query normalization; a measured candidate limit then bounds memory/CPU while explicitly preserving durable-knowledge priority and documenting recall trade-offs.
## Current state
- repositories.py:608-611 loads and decodes all scoped embeddings.
- retrieval.py:42-57 awaits KnowledgeRepository.get or MessageRepository.get once per embedding.
- vector.py:15-20 normalizes both vectors for every similarity calculation.
- service.py:1023-1028 repeats search for each profile scope.
- Settings and SAFE_RUNTIME_SETTING_TYPES are the repository's established path for editable safe runtime limits; new settings must update validation, formatting, Telegram/CLI exposure, tests, and docs.
Current loop:
    embeddings = await self.embedding_repo.list_for_retrieval(scope)
    for embedding, vector in embeddings:
        score = cosine_similarity(query_vector, vector)
        item = await self.knowledge_repo.get(embedding.target_id)
## Commands you will need
| Purpose | Command | Expected on success |
|---|---|---|
| Retrieval/settings tests | <code>uv run pytest -q tests/test_memory_correctness.py tests/test_memory_tools.py tests/test_operations.py</code> | all pass |
| Format/lint/type | <code>uv run ruff format --check .; uv run ruff check .; uv run ty check</code> | each exits 0 |
| Full tests | <code>uv run pytest -q</code> | all offline tests pass |
## Implementation result
The deterministic baseline measured 1,001 SQL statements for 1,000 candidates and 10,001 statements for 10,000 candidates. Retrieval now performs four statements at both corpus sizes, normalizes the query once, batch-hydrates knowledge and message targets, and applies a configurable positive `retrieval_candidate_limit` whose default 5,000 slots admit all valid durable knowledge before recent learned messages. The representative older durable fact remains eligible after 10,000 recent messages, and the focused 24-test target plus the complete 177-passed/1-skipped offline suite passed on 2026-07-29.
## Scope
**In scope**:
- src/pzp_ai_bot/memory/retrieval.py
- src/pzp_ai_bot/memory/vector.py
- src/pzp_ai_bot/memory/service.py
- src/pzp_ai_bot/db/repositories.py
- src/pzp_ai_bot/config.py
- tests/test_memory_correctness.py
- tests/test_memory_tools.py
- tests/test_operations.py
- docs/architecture.md
- docs/operations.md
- PLAN.md
**Out of scope**:
- Adding a native vector database, FAISS, or external service
- Changing knowledge-before-message ranking or Scope hierarchy
- Silently choosing a candidate cap without measuring the current representative corpus
- Git mutations
## Git workflow
- Read-only git inspection only; no stage, commit, push, branch, reset, or worktree.
## Steps
### Step 1: Establish a deterministic performance/query baseline
Add a non-network benchmark helper or focused test fixture that seeds 1,000 and 10,000 deterministic embeddings across knowledge and raw messages. Count SQL statements through SQLAlchemy events and record wall-clock timing outside strict CI assertions. The correctness gate must assert result ordering and query count, not a machine-dependent millisecond threshold.
**Verify**: baseline test demonstrates current query count grows with candidate count and records the before numbers in PLAN.md or docs/operations.md.
### Step 2: Normalize the query once
Add a vector helper that accepts a pre-normalized left vector or computes dot product against normalized candidates. Normalize query_vector once per search and each stored candidate once per candidate. Preserve zero-vector and dimension-mismatch behavior.
**Verify**: vector unit coverage proves numeric results match existing cosine_similarity within a tight tolerance.
### Step 3: Batch hydrate candidate targets
Add repository get_many methods for KnowledgeItem and Message keyed by ID. Retrieval must load embeddings once, split target IDs by type, hydrate each type in one query, and score in Python without per-candidate awaits. Ignore missing/deleted targets and unlearned messages exactly as today.
**Verify**: query-count test remains constant per scope as candidate count grows and search result content/order remains unchanged below the cap.
### Step 4: Add a safe runtime candidate limit
Add retrieval_candidate_limit to Settings and SAFE_RUNTIME_SETTING_TYPES with a conservative default of 5000 and positive validation. Thread it through runtime settings formatting/parsing/status/docs. Modify repository candidate selection to prefer durable knowledge embeddings and then most recent learned-message embeddings within the remaining quota, with deterministic ordering. Do not let raw messages crowd all durable knowledge out of the candidate set.
**Verify**: a seeded corpus larger than the limit decodes/hydrates no more than the configured count, returns top durable results present in the candidate set, and validates runtime edits.
### Step 5: Measure recall before accepting the bound
Create a representative test with an older durable fact plus many recent messages. If recency bounding removes the older durable fact at the proposed default, revise the quota strategy so durable knowledge remains eligible; if no deterministic bounded strategy preserves required recall, stop and report that an approximate vector index is required instead of shipping silent recall loss.
**Verify**: the representative older durable fact remains retrievable at the default bound.
### Step 6: Document behavior and update PLAN
Document the brute-force limit, durable/message quota, profile multiplication, tuning command, and when to migrate to a real vector index. Update PLAN.md performance baseline, tests, risks, version/date, and changelog.
**Verify**: <code>rg -n "retrieval_candidate_limit|candidate|vector index|durable" docs/architecture.md docs/operations.md PLAN.md</code> → design and trade-offs are explicit.
### Step 7: Run all gates
Run targeted tests, format, lint, ty, and full tests.
**Verify**: all exit 0 and only in-scope files are modified.
## Test plan
- Similarity numeric compatibility, zero vectors, and dimension mismatch.
- Constant bounded query count per scope.
- No more than candidate limit rows decoded/hydrated.
- Knowledge remains ranked before raw message results.
- Profile searches preserve scope authorization and final global top_k.
- Older durable facts remain eligible under the default policy.
- Runtime setting validates positive integers and appears in status.
## Done criteria
- [x] Retrieval has no per-candidate database awaits.
- [x] Query vector normalization occurs once per search.
- [x] Candidate work is bounded and deterministic.
- [x] Durable knowledge cannot be crowded out by raw messages.
- [x] Representative recall and ranking regressions pass.
- [x] Settings/docs/PLAN are synchronized.
- [x] Full gates pass.
## STOP conditions
- The measured representative corpus already exceeds the cap and required durable recall cannot be preserved deterministically.
- A proposed bound changes Scope containment or permission behavior.
- A new native/vector dependency is required; report a separate backend decision instead.
## Maintenance notes
- Revisit the cap with real corpus size/latency data; do not raise it indefinitely as a substitute for an indexed backend.
- Reviewers should inspect recall fixtures as closely as query-count improvements.
