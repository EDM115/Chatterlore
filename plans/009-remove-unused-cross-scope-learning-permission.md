# Plan 009: Remove the advertised but unused cross-scope learning permission
> **Executor instructions**: Follow each step and gate, stop on any STOP condition, and update plans/README.md when complete unless a reviewer owns it.
> **Drift check (run first)**: <code>git diff --stat beda837..HEAD -- src/pzp_ai_bot/telegram/router.py src/pzp_ai_bot/telegram/commands.py src/pzp_ai_bot/memory/service.py src/pzp_ai_bot/db/models.py src/pzp_ai_bot/db/repositories.py alembic/versions tests/test_memory_tools.py tests/test_telegram_integration.py docs/commands.md docs/architecture.md PLAN.md</code>.
## Status
- **Audit finding**: 8
- **Priority**: P2
- **Effort**: M
- **Risk**: MED
- **Depends on**: plans/006-gate-alembic-model-drift.md
- **Category**: bug
- **Planned at**: commit beda837, 2026-07-13
## Why this matters
The public /memory_profile_allow command accepts learn=on|off, the service persists can_learn, and PLAN.md calls learn/query permissions complete. Runtime cross-scope behavior only consults can_query plus the source scope's own learning policy, so can_learn is a misleading no-op consent control. The v1 architecture is retrieval-only across scopes; remove the unsupported surface and persisted column rather than inventing undefined cross-scope write semantics.
## Current state
- router.py:670-695 parses learn and passes can_learn.
- service.py:1459-1485 writes can_learn and includes it in audit payload.
- models.py:228-229 stores can_query and can_learn.
- service.py:1954-1982 checks only MemoryProfileRepository.can_query and source learning_enabled.
- telegram/commands.py:91-94 already documents only enabled query permission, while docs/commands.md:47 still advertises learn.
- tests/test_memory_tools.py:80-103 proves query permission and source learning policy behavior.
Current no-op:
    can_learn = _parse_on_off(args.get("learn", "off"))
    await context.memory.allow_memory_profile(..., can_query=enabled, can_learn=can_learn)
Runtime:
    if not await profile_repo.can_query(destination=destination, source=source):
        continue
## Commands you will need
| Purpose | Command | Expected on success |
|---|---|---|
| Profile tests | <code>.\.venv\Scripts\python.exe -m pytest -q tests/test_memory_tools.py tests/test_telegram_integration.py</code> | all pass |
| Migration | <code>.\.venv\Scripts\alembic.exe upgrade head; .\.venv\Scripts\alembic.exe check</code> | upgrade passes; no drift |
| Format/lint/type | <code>.\.venv\Scripts\ruff.exe format --check .; .\.venv\Scripts\ruff.exe check .; .\.venv\Scripts\ty.exe check</code> | each exits 0 |
| Full tests | <code>.\.venv\Scripts\python.exe -m pytest -q</code> | all offline tests pass |
## Scope
**In scope**:
- src/pzp_ai_bot/telegram/router.py
- src/pzp_ai_bot/telegram/commands.py
- src/pzp_ai_bot/memory/service.py
- src/pzp_ai_bot/db/models.py
- src/pzp_ai_bot/db/repositories.py
- one new Alembic revision
- tests/test_memory_tools.py
- tests/test_telegram_integration.py
- docs/commands.md
- docs/architecture.md
- PLAN.md
**Out of scope**:
- Implementing cross-scope learning or copying content between scopes
- Weakening source-scope learning policy or can_query checks
- Renaming profiles or Scope keys
- Git mutations
## Git workflow
- Use read-only git commands only; do not stage, commit, push, branch, reset, or use worktrees.
## Steps
### Step 1: Remove can_learn from the runtime API
Remove can_learn from MemoryService.allow_memory_profile, MemoryProfileRepository.set_permission, related view/format structures, and memory_profile_permission event payloads. Permission writes must update only can_query. Keep the source scope's own learning-enabled check unchanged.
**Verify**: <code>rg -n "can_learn" src tests docs PLAN.md</code> → only historical migration text may remain before the new migration is added.
### Step 2: Reject obsolete command input clearly
Remove learn from router usage and docs. Validate memory_profile_allow keys so learn= and any other unknown key return a clear unsupported-argument response before persistence. Keep enabled and scope behavior unchanged. Synchronize COMMAND_DESCRIPTIONS, command_help_text tests, router tests, and docs/commands.md as required by AGENTS.md.
**Verify**: a dispatcher test sends learn=on, sees an error, and records no permission mutation; enabled=on/off cases still pass.
### Step 3: Remove the database column
Create the next revision after the live head using Alembic batch operations compatible with SQLite to drop memory_scope_permissions.can_learn. Existing true values may be discarded because no runtime path ever consumed them; state that explicitly in the migration docstring and changelog. Update SQLAlchemy metadata.
**Verify**: fresh upgrade, upgrade from the prior revision with true/false seeded values, and alembic check all pass; can_query values survive.
### Step 4: Correct terminology and roadmap
Update docs/architecture.md and docs/commands.md to say cross-scope retrieval/query permission, not cross-chat learning permission. Correct PLAN.md requirements/tasks/gates/risks to distinguish local learning policy from cross-scope retrieval, update migration head/test map/date/version/changelog, and preserve historical changelog wording where it describes past state.
**Verify**: <code>rg -n "cross-chat learning|can_learn|learn=on" docs PLAN.md src tests</code> → no active contract advertises the removed permission.
### Step 5: Run all gates
Run profile tests, migrations, format, lint, ty, and full tests.
**Verify**: all exit 0 and only in-scope files are modified.
## Test plan
- Query permission disabled rejects profile retrieval.
- Query permission enabled plus learning-enabled source allows retrieval.
- Disabling source learning still blocks retrieval.
- learn= input is rejected without mutation.
- Upgrade preserves can_query while dropping can_learn.
- Fresh schema has no can_learn column and matches Alembic metadata.
## Done criteria
- [ ] No active command, service, repository, model, formatter, or doc exposes can_learn.
- [ ] Unknown learn= input fails without changing permission.
- [ ] can_query and source learning policy behavior remain intact.
- [ ] Migration preserves real permission data and passes drift check.
- [ ] PLAN uses retrieval/query terminology accurately.
- [ ] Targeted and full gates pass.
## STOP conditions
- A live runtime path, external API contract, or persisted workflow is found to read can_learn.
- Product documentation outside this repo defines concrete cross-scope learning semantics.
- Dropping the column would destroy another non-derivable permission.
## Maintenance notes
- If cross-scope learning is proposed later, require a separate threat model and explicit source/destination consent design rather than reusing retrieval permission implicitly.
