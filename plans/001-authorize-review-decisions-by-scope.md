# Plan 001: Authorize every review decision against the review's owning scope
> **Executor instructions**: Follow this plan step by step, run every verification command, and stop on any STOP condition. When complete, update this plan's row in plans/README.md unless a reviewer owns the index.
> **Drift check (run first)**: <code>git diff --stat beda837..HEAD -- src/pzp_ai_bot/telegram/router.py src/pzp_ai_bot/memory/service.py tests/test_telegram_integration.py tests/test_memory_quality.py docs/commands.md docs/privacy.md PLAN.md</code>. If live code no longer matches the excerpts below, stop and reconcile the plan before editing.
## Status
- **Audit finding**: 1
- **Priority**: P1
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none
- **Category**: security
- **Planned at**: commit beda837, 2026-07-13
## Why this matters
The memory review IDs are global database identifiers, but the Telegram approval and rejection handlers authorize only the chat where the command was sent. A Telegram administrator for one chat can therefore act on a known review ID belonging to another chat. The fix must load the pending review's scope before mutation and apply the existing target-scope authorization rules without weakening configured bot-admin access.
## Current state
- src/pzp_ai_bot/telegram/router.py owns Telegram authorization and already exposes require_scope_admin(message, scope).
- src/pzp_ai_bot/memory/service.py loads a pending review by ID and derives Scope only after the handler's current-chat require_admin check.
- tests/test_telegram_integration.py contains the established foreign-scope authorization pattern in test_chat_admin_cannot_mutate_foreign_scope.
- AGENTS.md requires require_admin for current-chat commands, configured bot-admin checks for global operations, Scope/parse_scope for scope semantics, safe_answer for replies, audit events for review operations, and synchronized command/docs/tests updates.
Current vulnerable shape:
    router.py:601-608
    if not await require_admin(message):
        return
    review_id = int((command.args or "").strip().split()[0])
    item_id = await context.memory.approve_review(review_id, reviewed_by=message_user_id(message))
    service.py:1654-1657
    review = await repo.get_pending(review_id)
    if review is None:
        raise ValueError(...)
    scope = Scope(review.chat_id, review.message_thread_id)
Existing authorization exemplar:
    router.py:590-598
    scope = parse_scope(args.get("scope", "this_topic"), current=scope_from_message(message))
    if not await require_scope_admin(message, scope):
        return
## Commands you will need
| Purpose | Command | Expected on success |
|---|---|---|
| Targeted Telegram tests | <code>.\.venv\Scripts\python.exe -m pytest -q tests/test_telegram_integration.py</code> | all tests pass |
| Targeted review tests | <code>.\.venv\Scripts\python.exe -m pytest -q tests/test_memory_quality.py</code> | all tests pass |
| Format check | <code>.\.venv\Scripts\ruff.exe format --check .</code> | exit 0; files already formatted |
| Lint | <code>.\.venv\Scripts\ruff.exe check .</code> | exit 0; no diagnostics |
| Types | <code>.\.venv\Scripts\ty.exe check</code> | exit 0; no diagnostics |
| Full tests | <code>.\.venv\Scripts\python.exe -m pytest -q</code> | all offline tests pass; telegram_live remains skipped |
Use the existing project virtual environment, never the system Python. If .venv is absent, stop and ask the operator which uv runtime is authorized rather than silently using an old global interpreter.
## Scope
**In scope**:
- src/pzp_ai_bot/telegram/router.py
- src/pzp_ai_bot/memory/service.py only if a read-only review lookup API is needed
- tests/test_telegram_integration.py
- tests/test_memory_quality.py only for service-level scope lookup coverage
- docs/commands.md
- docs/privacy.md
- PLAN.md
**Out of scope**:
- Changing review IDs, review persistence, confirmation tokens, or Scope key formats
- Reworking the broader review/Markdown lifecycle; that belongs to plan 002
- Granting ordinary chat admins access to foreign chats or global scope
- Any git stage, commit, push, branch, reset, or worktree operation
## Git workflow
- Do not create or switch branches, stage files, commit, push, or create worktrees; the user owns all additive git operations.
- Use read-only git status and diff commands to inspect scope.
## Steps
### Step 1: Add a read-only pending-review scope lookup
Add a MemoryService method such as pending_review_scope(review_id) that opens session_scope, calls MemoryReviewRepository.get_pending, raises the same pending-review-not-found ValueError, and returns Scope(review.chat_id, review.message_thread_id) without mutating the review. Reuse Scope; do not parse or reconstruct scope strings inline.
**Verify**: <code>.\.venv\Scripts\python.exe -m pytest -q tests/test_memory_quality.py</code> → all existing review tests pass.
### Step 2: Authorize the resolved target scope before approval or rejection
In both memory_approve and memory_reject, retain the initial admin requirement to reject ordinary members, parse the numeric ID, resolve its pending scope, call require_scope_admin(message, resolved_scope), and only then invoke approve_review or reject_review. A configured bot admin must still be allowed across scopes; a Telegram chat admin must be allowed only when the review belongs to the chat they administer.
**Verify**: <code>.\.venv\Scripts\python.exe -m pytest -q tests/test_telegram_integration.py -k "review or foreign_scope"</code> → selected tests pass.
### Step 3: Add cross-chat regression tests for both commands
Extend RouterMemoryDouble with deterministic review-to-scope lookup recording. Add parameterized tests proving: a chat admin cannot approve or reject a review owned by another chat; no mutation call occurs after denial; a chat admin can act on a review in the current chat; a configured bot admin can act on a foreign review; an unknown/non-pending ID returns the existing safe error. Use RecordingTelegramSession and message_update like the existing foreign-scope tests.
**Verify**: <code>.\.venv\Scripts\python.exe -m pytest -q tests/test_telegram_integration.py</code> → all tests pass, including both new denial cases.
### Step 4: Align the operator contract and roadmap
Update docs/commands.md to state that approval/rejection authorization is evaluated against the review's owning scope. Update docs/privacy.md only with the authorization boundary, without duplicating the full command reference. Update PLAN.md with the completed security correction, test coverage, risk closure, current date/version/changelog, and accurate DONE/PARTIAL state.
**Verify**: <code>rg -n "owning scope|review.*scope|memory_approve|memory_reject" docs/commands.md docs/privacy.md PLAN.md</code> → the new boundary and tests are discoverable.
### Step 5: Run all quality gates
Run formatting check, lint, ty, and the full offline test suite from the command table.
**Verify**: every command exits 0 and git status lists only in-scope files plus plans/README.md if the executor updates it.
## Test plan
- Model dispatcher tests after tests/test_telegram_integration.py:test_chat_admin_cannot_mutate_foreign_scope.
- Cover approve and reject independently; do not assume one handler proves the other.
- Assert the mutation double was not called when authorization fails.
- Preserve configured-bot-admin, current-chat-admin, unknown-ID, and malformed-ID behavior.
## Done criteria
- [ ] A review's owning Scope is resolved before either mutation.
- [ ] require_scope_admin evaluates that Scope for both approve and reject.
- [ ] Foreign-chat administrators cannot mutate another chat's review even with a valid ID.
- [ ] Configured bot admins and same-chat administrators retain intended access.
- [ ] Audit events still record the actual review scope and reviewer ID.
- [ ] Command/privacy docs and PLAN.md match the new behavior.
- [ ] Targeted and full quality gates pass.
- [ ] No out-of-scope files or additive git operations were used.
## STOP conditions
- The pending review can no longer be resolved without mutating it.
- require_scope_admin semantics differ from the current configured-bot-admin/current-chat-admin boundary.
- A correct fix appears to require putting a user-supplied scope back into the approve/reject command.
- Any test exposes review content in an unauthorized response or log.
- A verification command fails twice after a reasonable correction.
## Maintenance notes
- Any future command that mutates a globally identified object must authorize the object's persisted Scope, not merely the chat where the command arrives.
- Reviewers should verify the lookup and mutation cannot target different review rows and that denial happens before all mutation calls.
- Plan 002 separately fixes when review content is allowed into Markdown; do not fold that larger lifecycle change into this patch.
