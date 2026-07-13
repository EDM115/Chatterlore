# Plan 017: Evaluate learning policy from one immutable snapshot per operation
> **Executor instructions**: Follow every step and gate, preserve policy semantics exactly, and update plans/README.md when complete unless a reviewer owns it.
> **Drift check (run first)**: <code>git diff --stat beda837..HEAD -- src/pzp_ai_bot/memory/policy.py src/pzp_ai_bot/memory/service.py src/pzp_ai_bot/db/repositories.py tests/test_policy_and_ingestion.py tests/test_memory_correctness.py tests/test_product_controls.py docs/architecture.md PLAN.md</code>.
## Status
- **Audit finding**: lower-priority policy-query opportunity
- **Priority**: P3
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none
- **Category**: perf
- **Planned at**: commit beda837, 2026-07-13
## Why this matters
One learning decision currently reloads the entire learning_policies table for scope enablement, user denial, allowlist detection, user allow, and admin allow checks. Known-scope reporting then calls status per scope, reloading policies twice each time. A single immutable snapshot per decision or report preserves exact hierarchy semantics while reducing common-path database scans from four or five to one.
## Current state
- policy.py:77-90 calls five helpers during a decision.
- policy.py:98, 113, 123, 133, and 140 each call repository.list_policies.
- repositories.py:691-692 performs an unfiltered SELECT for every call.
- service.py:590 loads policies once for scope discovery but service.py:602-607 then calls policies.status for each scope, causing more reloads.
Current helper:
    async def _user_denied(...):
        policies = await self.repository.list_policies()
        return any(...)
## Commands you will need
| Purpose | Command | Expected on success |
|---|---|---|
| Policy tests | <code>.\.venv\Scripts\python.exe -m pytest -q tests/test_policy_and_ingestion.py tests/test_memory_correctness.py tests/test_product_controls.py</code> | all pass |
| Format/lint/type | <code>.\.venv\Scripts\ruff.exe format --check .; .\.venv\Scripts\ruff.exe check .; .\.venv\Scripts\ty.exe check</code> | each exits 0 |
| Full tests | <code>.\.venv\Scripts\python.exe -m pytest -q</code> | all offline tests pass |
## Scope
**In scope**:
- src/pzp_ai_bot/memory/policy.py
- src/pzp_ai_bot/memory/service.py
- tests/test_policy_and_ingestion.py
- tests/test_memory_correctness.py
- tests/test_product_controls.py
- docs/architecture.md
- PLAN.md
**Out of scope**:
- Caching policies across transactions or runtime refreshes
- Changing policy precedence, Scope containment, allowlist behavior, or defaults
- Adding a global mutable cache
- Git mutations
## Git workflow
- Read-only git inspection only; no stage, commit, push, branch, reset, or worktree.
## Steps
### Step 1: Define an immutable policy snapshot
Add a PolicySnapshot dataclass or tuple wrapper constructed from one repository.list_policies call. Keep contained LearningPolicy values read-only for the duration of one session operation. Move _scope_enabled, _user_denied, _user_allowed, _has_allowlist, and _admins_allowed to pure snapshot evaluators.
**Verify**: ty check passes and pure evaluator tests cover chat/topic/global precedence.
### Step 2: Load once in decide and status
PolicyService.decide must load one snapshot after fast rejections for bot/command/empty text, then evaluate every policy rule from it. status accepts an optional snapshot or loads exactly one when called alone.
**Verify**: a SQLAlchemy query counter observes one learning_policies SELECT per enabled decision and one per standalone status.
### Step 3: Reuse one snapshot for known-scopes reporting
In MemoryService.known_scopes, load one snapshot before the scope loop and pass it to each status evaluation. Reuse the same row set already needed to discover policy-only scopes. Do not cache beyond that session.
**Verify**: known_scopes over many scopes performs one policy-table SELECT and returns unchanged labels/status counts.
### Step 4: Add semantic and query-count regressions
Cover global default, chat override, topic override, user deny, allowlist, allowed user, admin allowance, missing user, and policy-only scope reporting. Add query-count assertions that are independent of unrelated metadata queries.
**Verify**: targeted tests pass and policy query count is constant.
### Step 5: Document and update PLAN
Add a short architecture note that policy evaluation uses transaction-local immutable snapshots, not cross-request caching. Update PLAN.md performance/tests/date/version/changelog.
**Verify**: <code>rg -n "policy snapshot|learning_policies|one.*query" docs/architecture.md PLAN.md</code> → optimization boundary is clear.
### Step 6: Run all gates
Run all commands above.
**Verify**: all exit 0 and only in-scope files are modified.
## Test plan
- Every existing precedence/allowlist case returns the same LearnDecision reason.
- Enabled common path issues one policy SELECT.
- known_scopes issues one policy SELECT regardless of scope count.
- Mutations in a later transaction are visible to the next decision; no stale global cache exists.
## Done criteria
- [ ] One decision/status batch loads one policy snapshot.
- [ ] Policy semantics and reason strings remain unchanged.
- [ ] No cross-request mutable cache exists.
- [ ] Query-count and semantic tests pass.
- [ ] Architecture docs, PLAN, and full gates pass.
## STOP conditions
- A helper relies on observing policy mutations performed later in the same logical operation.
- The refactor changes precedence or LearnDecision reason strings.
- The only solution proposed is a process-global cache without invalidation.
## Maintenance notes
- Extend the snapshot with new policy types rather than adding another repository scan.
