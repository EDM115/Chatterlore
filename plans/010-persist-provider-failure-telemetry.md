# Plan 010: Persist provider failure telemetry after business rollback
> **Executor instructions**: Execute only after plan 008 establishes provider calls outside write transactions. Follow every gate and update plans/README.md when complete unless a reviewer owns it.
> **Drift check (run first)**: <code>git diff --stat beda837..HEAD -- src/pzp_ai_bot/memory/service.py src/pzp_ai_bot/db/repositories.py src/pzp_ai_bot/db/models.py tests/test_ai_providers.py tests/test_memory_correctness.py docs/operations.md docs/architecture.md PLAN.md</code>.
## Status
- **Audit finding**: 11
- **Priority**: P2
- **Effort**: M
- **Risk**: MED
- **Depends on**: plans/008-bound-and-claim-ai-work.md
- **Category**: bug
- **Planned at**: commit beda837, 2026-07-13
## Why this matters
AIUsage supports error records, but embedding failures currently add telemetry in the same session and then re-raise. session_scope rolls back that row with the failed business operation, so ai_usage_errors underreports failures. Telemetry must survive only after the business transaction has rolled back or released its claim, without committing partial memory state or opening a competing SQLite writer inside an active write transaction.
## Current state
- service.py:2058-2098 catches provider error, adds AIUsage in finally, then re-raises.
- session.py:67-74 rolls the entire active session back when that exception propagates.
- repositories.py:1451-1476 adds AIUsage to the supplied session.
- tests/test_ai_providers.py:184-214 covers successful embedding usage only.
Current shape:
    except Exception as exc:
        error = str(exc)
        raise
    finally:
        await EventRepository(session).add_ai_usage(..., error=error)
## Commands you will need
| Purpose | Command | Expected on success |
|---|---|---|
| Provider/correctness tests | <code>.\.venv\Scripts\python.exe -m pytest -q tests/test_ai_providers.py tests/test_memory_correctness.py</code> | all pass |
| Format/lint/type | <code>.\.venv\Scripts\ruff.exe format --check .; .\.venv\Scripts\ruff.exe check .; .\.venv\Scripts\ty.exe check</code> | each exits 0 |
| Full tests | <code>.\.venv\Scripts\python.exe -m pytest -q</code> | all offline tests pass |
## Scope
**In scope**:
- src/pzp_ai_bot/memory/service.py
- src/pzp_ai_bot/db/repositories.py
- tests/test_ai_providers.py
- tests/test_memory_correctness.py
- docs/operations.md
- docs/architecture.md
- PLAN.md
**Out of scope**:
- SQL aggregation/indexes for usage data; plan 018 owns that
- Logging prompt/message content or unbounded raw exception text
- Committing failed business data for the sake of telemetry
- A background telemetry service or external vendor
- Git mutations
## Git workflow
- Read-only git inspection only; no staging, commits, pushes, branches, resets, or worktrees.
## Steps
### Step 1: Introduce a safe provider-call telemetry record
Define a typed immutable value for provider, model, operation, latency, token counts when available, error type, bounded/redacted error summary, and metadata such as Scope key/count/dimensions/profile/prompt version. Provider helpers should return this record on success or attach it to a typed provider-operation exception on failure. Never include prompts, source text, API keys, URLs with credentials, or arbitrary response bodies.
**Verify**: ty check covers every embedding/chat provider helper and caller.
### Step 2: Persist telemetry in a fresh short transaction at the correct boundary
After plan 008, provider calls occur outside business write sessions. On success, persist usage with the successful business finalization when atomicity is useful. On failure, first release/roll back any business claim/state, then open a separate session_scope solely to add AIUsage, close it, and re-raise the original domain/provider error. Never open the telemetry writer while another service write transaction is active.
**Verify**: an instrumented test observes rollback/release before the AIUsage insert and no partial KnowledgeItem/Embedding.
### Step 3: Cover every provider operation consistently
Inventory all embedding and chat call sites and route their success/failure records through the same helper: message embedding, knowledge embedding, query embedding, item refresh, imports, summarization, range summary, answer generation, and canonical compaction. Preserve operation names used by existing status output.
**Verify**: <code>rg -n "add_ai_usage|embedding_provider\\.embed|chat_provider\\.generate" src/pzp_ai_bot</code> → every direct provider call has an explicit telemetry boundary.
### Step 4: Add regression tests
Use failing provider doubles for at least embed_message and embed_query. Assert the business state is absent or retryable as designed, exactly one AIUsage row persists with error populated, Scope metadata is queryable, and a unique raw-text sentinel is absent from error and metadata. Add a success assertion to prevent duplicate usage rows.
**Verify**: targeted provider/correctness tests pass.
### Step 5: Document telemetry atomicity and update PLAN
Explain in docs/operations.md that provider failure telemetry is committed independently after business rollback and contains metadata only. Document the transaction boundary in docs/architecture.md. Update PLAN.md observability tests/risks/date/version/changelog.
**Verify**: <code>rg -n "failure telemetry|rollback|AIUsage|metadata only" docs/operations.md docs/architecture.md PLAN.md</code> → semantics are explicit.
### Step 6: Run all gates
Run all commands above.
**Verify**: all exit 0 and only in-scope files are modified.
## Test plan
- Failed message embedding persists one error row and no message/embedding partial state beyond the pipeline's documented policy.
- Failed query embedding persists one error row and returns no fabricated results.
- Summary/chat failure remains retryable as established by plan 003.
- Raw sentinel and secrets are absent from telemetry.
- Success paths still produce exactly one usage row.
## Done criteria
- [ ] Failed provider calls persist one AIUsage error after business rollback/release.
- [ ] No failed business mutation is committed for telemetry.
- [ ] No nested competing SQLite writer is opened during an active write transaction.
- [ ] All provider call sites use one safe record shape.
- [ ] Operations/architecture docs and PLAN are current.
- [ ] Targeted and full gates pass.
## STOP conditions
- Plan 008 is not complete and provider calls still execute inside active write sessions.
- The only proposed solution requires committing partial business data.
- Safe redaction cannot guarantee provider exception content is free of prompts or secrets.
## Maintenance notes
- New provider operations must declare their operation name, safe metadata, and success/failure persistence boundary.
- Reviewers should verify error counts with a real rollback path, not a direct repository insert.
