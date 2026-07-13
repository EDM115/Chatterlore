# Plan 020: Split service, repository, and router hotspots behind compatible facades
> **Executor instructions**: This is a behavior-preserving final refactor. Execute only after the behavioral plans touching these files are DONE or REJECTED. Follow each phase and gate, and update plans/README.md unless a reviewer owns it.
> **Drift check (run first)**: <code>git diff --stat beda837..HEAD -- src/pzp_ai_bot/memory/service.py src/pzp_ai_bot/db/repositories.py src/pzp_ai_bot/telegram/router.py tests docs/architecture.md PLAN.md</code>. Extensive expected drift from prerequisite plans requires refreshing excerpts and inventories before implementation.
## Status
- **Audit finding**: lower-priority architecture hotspot opportunity
- **Priority**: P3
- **Effort**: L
- **Risk**: MED
- **Depends on**: plans/001-authorize-review-decisions-by-scope.md, plans/002-rebuild-markdown-from-committed-memory.md, plans/003-keep-provider-failures-retryable.md, plans/004-supervise-background-runtime-tasks.md, plans/005-validate-allow-admins-input.md, plans/007-enforce-concurrent-message-idempotency.md, plans/008-bound-and-claim-ai-work.md, plans/009-remove-unused-cross-scope-learning-permission.md, plans/010-persist-provider-failure-telemetry.md, plans/011-bound-and-batch-retrieval.md, plans/012-compact-only-dirty-scopes.md, plans/017-load-policy-snapshots-once.md, plans/018-aggregate-and-index-telemetry.md, plans/019-stream-and-batch-bulk-transfer.md
- **Category**: tech-debt
- **Planned at**: commit beda837, 2026-07-13
## Why this matters
memory/service.py combines policy, ingestion, retrieval, providers, compaction, transfer, retention, notes, reviews, profiles, templates, parsing, formatting, and telemetry. db/repositories.py and telegram/router.py similarly centralize unrelated slices. This increases merge conflicts and makes transaction/privacy changes difficult to review. Extract cohesive modules while preserving MemoryService, repository imports, build_context wiring, command behavior, and all public formatting/parsing contracts.
## Current state
- MemoryService begins at service.py:294 and orchestration/helpers continue beyond line 3100.
- repositories.py contains message, knowledge, embedding, policy, settings, profile, challenge, event, and usage repositories in one roughly 1,580-line module.
- build_router defines nearly every Telegram handler in one closure.
- AGENTS.md requires build_context wiring, session_scope/repositories, centralized parser/formatter reuse, command/help/docs coupling, and the context fixture.
## Commands you will need
| Purpose | Command | Expected on success |
|---|---|---|
| Full tests after every extraction phase | <code>.\.venv\Scripts\python.exe -m pytest -q</code> | all offline tests pass |
| Format | <code>.\.venv\Scripts\ruff.exe format --check .</code> | exit 0 |
| Lint | <code>.\.venv\Scripts\ruff.exe check .</code> | exit 0 |
| Types | <code>.\.venv\Scripts\ty.exe check</code> | exit 0 |
| Import inventory | <code>rg -n "from pzp_ai_bot\.(memory\.service|db\.repositories|telegram\.router) import" src tests</code> | every compatibility import is understood |
## Scope
**In scope**:
- src/pzp_ai_bot/memory/service.py
- new cohesive modules under src/pzp_ai_bot/memory
- src/pzp_ai_bot/db/repositories.py
- new repository modules under src/pzp_ai_bot/db/repos
- src/pzp_ai_bot/telegram/router.py
- new router modules under src/pzp_ai_bot/telegram/routers
- tests needed for characterization/import compatibility
- docs/architecture.md
- PLAN.md
**Out of scope**:
- Any intentional behavior, schema, command, output-text, authorization, transaction, or storage change
- Renaming the MemoryService facade or changing build_context callers
- Removing compatibility re-exports in the same pass
- A new framework, dependency-injection container, or plugin system
- Git mutations
## Git workflow
- Read-only git inspection only; no stage, commit, push, branch, reset, or worktree.
- Keep each extraction phase independently testable; the user decides commits.
## Steps
### Step 1: Refresh inventories and add characterization guards
After all dependencies, inventory MemoryService public methods, repository classes, router commands, parser/formatter imports, and build_context construction. Add tests asserting the public facade/imports and command list remain available. Snapshot only stable semantic output, not volatile whitespace.
**Verify**: full tests pass before moving code and import inventory is recorded in PLAN.md or the plan execution notes.
### Step 2: Extract repository implementations behind the existing module
Create src/pzp_ai_bot/db/repos with cohesive modules such as messages.py, knowledge.py, embeddings.py, policy.py, settings.py, profiles.py, challenges.py, and events.py. Keep src/pzp_ai_bot/db/repositories.py as a compatibility facade re-exporting the same classes, dataclasses, and helpers with unchanged names/signatures. Avoid circular imports by moving shared scope_filter/value types to one explicit shared module.
**Verify**: full tests, ty, and import-compatibility tests pass; existing source imports need not all change immediately.
### Step 3: Extract MemoryService feature slices
Keep MemoryService as the public facade constructed by build_context. Extract internal collaborators with explicit dependencies: query/retrieval, mutation/reviews/retention, summary/compaction, transfer/import-export, settings/profiles/templates, and telemetry. Move methods one slice at a time, delegating from the facade with identical async signatures and return values. Keep parser/format helpers available from memory.service until callers are migrated and compatibility is proven.
**Verify**: run full tests after each slice; no phase proceeds with failures.
### Step 4: Split Telegram routers by command family
Create feature router builders for learning policy, memory/reviews, profiles/templates/settings, retrieval/summarization, and operations. The root build_router composes them and owns shared authorization/safe reply helpers or imports them from one shared module. Preserve COMMAND_DESCRIPTIONS, registration order where observable, message-size limits, and every handler's authorization.
**Verify**: tests/test_telegram_commands.py and tests/test_telegram_integration.py pass after each router family extraction, followed by full tests.
### Step 5: Tighten dependency direction
Ensure telegram modules depend on the MemoryService facade/protocol, feature services depend on repositories/providers/Markdown, repositories depend on models/Scope, and lower layers never import Telegram. Resolve cycles by defining small typed protocols/value objects, not service locators or global state.
**Verify**: ty passes and an import graph/search finds no db or memory module importing pzp_ai_bot.telegram.
### Step 6: Remove only proven dead compatibility code
After all internal imports use the new modules and full tests pass, keep public facade re-exports that external/local tools may use. Remove only duplicates proven unused by rg and tests. Do not combine cleanup with behavior changes.
**Verify**: import-compatibility tests and full suite pass.
### Step 7: Document architecture and update PLAN
Update docs/architecture.md with module responsibilities, dependency direction, transaction ownership, and stable facades. Update PLAN.md file ownership map, test ownership, risks, version/date/changelog. Do not duplicate command/setup material.
**Verify**: every new module appears once in the architecture/ownership map and obsolete ownership paths are gone.
### Step 8: Run all final gates
Run format, lint, ty, full tests, and read-only git diff/status.
**Verify**: all exit 0; there are no behavior/schema/docs changes beyond the stated refactor and architecture/roadmap updates.
## Test plan
- Characterize every public MemoryService method group before extraction.
- Assert imports from memory.service and db.repositories remain valid.
- Run Telegram command matrix and help registration after router splits.
- Preserve exact authorization, audit event types, parser errors, formatter semantics, transaction boundaries, and provider call counts established by prerequisite plans.
- Run full suite after every phase, not only at the end.
## Done criteria
- [ ] MemoryService remains the build_context-facing facade with unchanged public behavior.
- [ ] Repository compatibility imports remain valid.
- [ ] Router command/help/authorization behavior is unchanged.
- [ ] Dependency direction has no lower-layer Telegram imports or new cycles.
- [ ] No schema or public behavior change is hidden in the refactor.
- [ ] Architecture docs, PLAN, and all gates pass.
## STOP conditions
- Any prerequisite touching the three hotspot files is still IN PROGRESS.
- A public import/caller outside the inventoried repo cannot be assessed.
- An extraction requires a behavior, schema, or transaction change.
- Full tests fail after an extraction phase twice; revert only that phase through careful edits, not destructive git commands, and report.
## Maintenance notes
- Future features should enter the owning slice while MemoryService remains a stable orchestration facade.
- Reviewers should reject speculative abstractions, parallel parsers/formatters, and service-locator patterns.
