# Plan 014: Reconcile PLAN.md with the current repository and release gates
> **Executor instructions**: This is documentation-only. Inspect the live tree first, change only PLAN.md and the plan index, and update plans/README.md when complete unless a reviewer owns it.
> **Drift check (run first)**: <code>git diff --stat beda837..HEAD -- PLAN.md tests docs/testing.md pyproject.toml</code>. If implementation or test names changed, remap from the live tree before editing.
## Status
- **Status**: DONE for the original audit scope, reconciled again on 2026-11-07 as ongoing roadmap maintenance
- **Audit finding**: 14
- **Priority**: P2
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none
- **Category**: docs
- **Planned at**: commit beda837, 2026-07-13
## Why this matters
PLAN.md is the project's living lifecycle roadmap, but its header remains v0.11 while the changelog records v0.12, its test/ownership maps reference removed phase-named files, and Gate D says v1 is complete while Gate C still has required live beta TODOs. Contributors cannot use it as a trustworthy North Star until state, ownership, and release-gate semantics match the current tree.
## Current state
- PLAN.md:3-7 says version 0.11, last_updated 2026-06-24, status In progress.
- PLAN.md:316-323 and 499 reference removed test_phase*.py files.
- docs/testing.md:16-20 accurately names the behavior-oriented replacement test files.
- PLAN.md:383-391 leaves three Gate C TODOs.
- PLAN.md:398-408 marks every Gate D implementation item DONE without stating whether incomplete Gate C blocks v1.
- PLAN.md:549 already records v0.12.
Verified replacement mapping:
- test_phase1.py → test_memory_correctness.py
- test_phase2.py → test_memory_quality.py
- test_phase2_remaining.py → test_memory_tools.py
- test_phase3.py → test_ai_providers.py
- test_phase4_telegram.py → test_telegram_integration.py
- test_phase5.py → test_operations.py
- test_phase6.py → test_product_controls.py
## Commands you will need
| Purpose | Command | Expected on success |
|---|---|---|
| List tests | <code>rg --files tests</code> | every referenced test exists |
| Check status markers | <code>rg -n "DONE|PARTIAL|TODO|Gate C|Gate D|version:|last_updated:" PLAN.md</code> | states are explicit |
| Verify references | <code>rg -o "tests/[A-Za-z0-9_./-]+\.py" PLAN.md</code> | every path exists |
| Diff scope | <code>git diff --name-only</code> | only PLAN.md and plans/README.md for this execution |
| Diff hygiene | <code>git diff --check -- PLAN.md</code> | clean, except intentional two-space hard breaks allowed by AGENTS.md |
Do not run the application suite for this documentation-only change.
## Scope
**In scope**:
- PLAN.md
- plans/README.md status row
**Out of scope**:
- Source, tests, README.md, or docs/*.md
- Reclassifying code behavior without current evidence
- Claiming live Telegram, hosted CI, or Docker validation that was not run
- Git mutations
## Git workflow
- Read-only status/diff only; no stage, commit, push, branch, reset, or worktree.
## Steps
### Step 1: Re-inventory current facts
Read pyproject.toml version, current Alembic head, tests directory, docs/testing.md coverage map, current CI workflow, and all Gate C/D evidence. Build a private checklist; do not copy setup/command material into PLAN.md because README/docs remain canonical.
**Verify**: every test and revision path you will name exists.
### Step 2: Repair header/version metadata
Advance the roadmap version monotonically from the latest changelog value, set last_updated to the execution date, and set status to a precise lifecycle phrase such as In progress — private beta validation pending. Do not overwrite date_created or historical changelog entries.
**Verify**: header version is later than 0.12 and matches the new final changelog entry.
### Step 3: Replace obsolete test and ownership references
Use the verified behavior-oriented file mapping above, then reconcile descriptions against docs/testing.md and the live tests. Include test_webhook.py, test_telegram_commands.py, test_admin_service.py, test_deployment_artifacts.py, test_telegram_live.py, and conftest.py where appropriate. Remove active references to non-existent phase test files while preserving historical migration filenames.
**Verify**: a PowerShell or manual path check confirms every tests/*.py path extracted from PLAN.md exists.
### Step 4: Clarify release-gate semantics
Keep each Gate C item honestly DONE or TODO. State explicitly that Gate D implementation work may be complete while v1 release readiness remains PARTIAL/BLOCKED until required Gate C live validations pass. Do not turn unperformed live checks into DONE. Reconcile Gate D wording so the overall gate cannot be read as complete.
**Verify**: Gate C TODOs and Gate D overall status are logically consistent when read alone.
### Step 5: Reconcile implementation metadata and append one changelog entry
Correct stale current-version/migration/test-count/file-map facts found during Step 1, without rewriting historical entries. Append one dated changelog item describing roadmap reconciliation only.
**Verify**: <code>rg -n "test_phase|v0\.11|v0\.12|Gate C|Gate D" PLAN.md</code> → obsolete active references are gone; historical changelog entries remain.
### Step 6: Validate documentation-only scope
Run reference, status, diff-name, and diff-hygiene commands.
**Verify**: only PLAN.md plus plan-index status changed and all referenced files exist.
## Test plan
- No application tests; this is documentation-only per AGENTS.md.
- Mechanically verify file paths, version monotonicity, status consistency, and diff scope.
## Done criteria
- [x] Header version/date/status match the latest roadmap state.
- [x] Active test/file references were reconciled with the current tree, dated references remain historical.
- [x] Gate C, Gate D and the added control-plane Gate E distinguish implementation from remaining release acceptance.
- [x] Historical changelog entries remain intact and new entries record later reconciliation.
- [x] The original documentation-only plan scope is preserved, the separately authorized November maintenance pass also includes dependency, test and frontend changes.
## STOP conditions
- Live repo state cannot determine whether a gate has actually passed.
- Another in-progress change is simultaneously editing PLAN.md.
- Reconciliation would require claiming an external/manual verification without evidence.
## Maintenance notes
- Every meaningful implementation plan in this directory instructs its executor to update PLAN.md; execute this reconciliation after a batch if those updates would otherwise immediately supersede it.
