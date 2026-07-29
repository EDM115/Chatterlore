# Plan 003: Keep summarization failures retryable without persisting raw chat
> **Executor instructions**: Follow every step and verification gate. Update plans/README.md when complete unless a reviewer owns the index.
> **Drift check (run first)**: <code>git diff --stat beda837..HEAD -- src/pzp_ai_bot/memory/service.py tests/test_memory_quality.py tests/test_ai_providers.py docs/privacy.md docs/operations.md PLAN.md</code>. Stop if the summarized/error flow no longer matches this plan.
## Status
- **Implementation status**: DONE (2026-07-29)
- **Audit finding**: 3
- **Priority**: P1
- **Effort**: M
- **Risk**: MED
- **Depends on**: none
- **Category**: bug
- **Planned at**: commit beda837, 2026-07-13
## Why this matters
When the chat provider raises, the current fallback joins every pending raw message into content, marks those messages summarized, and stores the content as durable knowledge or a review proposal. This violates the prompt and privacy contract and prevents a later successful retry. Failure must leave the batch pending, persist content-free diagnostics, and create no knowledge, review, or Markdown.
## Current state
- service.py:1852-1856 explicitly instructs the provider not to dump raw chat.
- service.py:1878-1884 converts an exception into raw message lines instead of a failed outcome.
- service.py:1786-1819 marks all pending messages summarized before deciding whether the generated content is valid.
- tests/test_memory_quality.py provides batch/review workflow structure; tests/test_ai_providers.py provides provider doubles and AI usage assertions.
Current failure shape:
    except Exception as exc:
        error = str(exc)
        content = "\n".join(f"- {message.normalized_text} [source:{message.telegram_message_id}]" for message in pending)
        result = None
    ...
    await MessageRepository(session).mark_summarized([message.id for message in pending])
## Commands you will need
| Purpose | Command | Expected on success |
|---|---|---|
| Targeted tests | <code>.\.venv\Scripts\python.exe -m pytest -q tests/test_memory_quality.py tests/test_ai_providers.py</code> | all pass |
| Format | <code>.\.venv\Scripts\ruff.exe format --check .</code> | exit 0 |
| Lint | <code>.\.venv\Scripts\ruff.exe check .</code> | exit 0 |
| Types | <code>.\.venv\Scripts\ty.exe check</code> | exit 0 |
| Full tests | <code>.\.venv\Scripts\python.exe -m pytest -q</code> | all offline tests pass |
## Scope
**In scope**:
- src/pzp_ai_bot/memory/service.py
- tests/test_memory_quality.py
- tests/test_ai_providers.py
- docs/privacy.md
- docs/operations.md
- PLAN.md
**Out of scope**:
- Retry/backoff inside provider implementations
- General transaction splitting and batch claiming; plan 008 owns that
- Embedding failure telemetry rollback; plan 010 owns that
- Persisting raw text in errors, events, logs, or test snapshots
- Git mutation
## Git workflow
- Use only read-only git inspection. Do not stage, commit, push, branch, reset, or create worktrees.
## Steps
### Step 1: Represent summary generation failure explicitly
Change the private summary result shape so provider failure yields no content and carries only safe metadata such as provider, model, operation, latency, exception type, and a bounded/redacted error. A successful empty response is also a failure. Do not synthesize content from pending messages.
**Verify**: ty check passes for every caller of _summarize_messages_in_session.
### Step 2: Preserve pending state on failure
In _summarize_pending_in_session, record the content-free AI usage/error event first, then return a failed outcome before mark_summarized, MemoryReviewRepository.add, _add_knowledge_in_session, or any Markdown operation. Make ingest.summarized and summarize_now counts represent completed summaries, not attempted batches.
**Verify**: a failing provider leaves summarized_at null for every source and returns zero completed summaries.
### Step 3: Add safe operational diagnostics
Add a summary_failed audit event containing Scope, source message IDs, provider/model metadata, and exception type only. Do not include normalized_text, prompts, provider secrets, or raw exception strings that may contain request data. Preserve the existing ai_usage error record when the transaction can commit safely.
**Verify**: inspect the recorded event/usage payload in a test and assert a unique raw-message sentinel is absent.
### Step 4: Add failure-and-retry regression tests
Create a provider double that fails once and succeeds once. Assert after failure: zero knowledge items, zero pending reviews, no raw sentinel in daily or canonical Markdown, all messages pending, and one safe failure diagnostic. Retry and assert exactly one durable summary/review, source messages marked once, and no duplicated knowledge.
**Verify**: <code>.\.venv\Scripts\python.exe -m pytest -q tests/test_memory_quality.py tests/test_ai_providers.py</code> → all pass.
### Step 5: Update privacy/operations docs and PLAN
Document that provider failure leaves messages pending and never promotes raw chat to durable memory. Record the regression test and correction in PLAN.md with accurate status/version/date/changelog.
**Verify**: <code>rg -n "provider failure|pending|raw chat|retry" docs/privacy.md docs/operations.md PLAN.md</code> → behavior is explicit.
### Step 6: Run all gates
Run format, lint, ty, targeted tests, and full tests.
**Verify**: all exit 0 and only in-scope files are modified.
## Test plan
- Fail before any provider result exists and fail with an empty result.
- Cover review_before_compact on and off.
- Assert raw sentinel absence across KnowledgeItem, MemoryReviewItem, daily Markdown, MEMORY.md, bot_events, and AIUsage metadata.
- Prove a subsequent retry succeeds once and source IDs remain correct.
## Done criteria
- [x] Provider failure produces no synthetic raw summary.
- [x] Failed source messages remain unsummarized and retryable.
- [x] No knowledge, review proposal, or Markdown content is created on failure.
- [x] Diagnostics are content-free and scoped.
- [x] Completion counts distinguish attempts from successful summaries.
- [x] Retry regression passes for review mode on and off.
- [x] Docs, PLAN, targeted tests, and full gates pass.

Verification on 2026-07-29 used `uv run`: exception and empty-response regressions passed with review mode both off and on, the ingest completion-count regression passed, the complete `tests/test_memory_quality.py` and `tests/test_ai_providers.py` target passed, Ruff format and lint passed, `ty check` passed, and the full offline pytest suite passed.
## STOP conditions
- A caller requires a string on failure and would persist it without checking outcome status.
- Safe diagnostics cannot be stored without raw content or secrets.
- Correct retry semantics require the larger claim/transaction design from plan 008; stop and execute that dependency first rather than improvising.
## Maintenance notes
- Every future fallback must be classified as either safe derived content or failure; raw source concatenation is never a summary.
- Reviewers should trace the failure path through counts, state flags, review creation, Markdown, and retry.
