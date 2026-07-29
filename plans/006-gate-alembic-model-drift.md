# Plan 006: Fail CI when SQLAlchemy metadata drifts from Alembic history
> **Executor instructions**: Follow every step and verification gate, then update plans/README.md unless a reviewer owns it.
> **Drift check (run first)**: <code>git diff --stat beda837..HEAD -- .github/workflows/ci.yml alembic/env.py tests/test_deployment_artifacts.py docs/testing.md PLAN.md</code>.
## Status
- **Status**: DONE
- **Audit finding**: 13
- **Priority**: P2
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none
- **Category**: migration
- **Planned at**: commit beda837, 2026-07-13
## Why this matters
CI upgrades an empty database to head, but that proves only that existing revisions execute. A model-only column, constraint, index, default, or nullability change can pass while migration history remains incomplete. Alembic already receives Base.metadata, so an explicit post-upgrade alembic check should reject drift before schema-bearing plans land.
## Current state
- alembic/env.py:16 sets target_metadata = Base.metadata.
- .github/workflows/ci.yml:46-47 runs only uv run alembic upgrade head.
- tests/test_deployment_artifacts.py:26-41 verifies the upgrade command exists but not metadata drift detection.
- AGENTS.md requires schema changes to include models, a revision, and focused upgrade/runtime tests.
Current gate:
    - name: Migration check
      run: uv run alembic upgrade head

Execution on 2026-07-29 found real drift in nine Telegram identifier columns: the live SQLAlchemy models used `BigInteger` while the initial migration still declared `Integer`. Revision `0007_align_telegram_identifier_types` now aligns the history without suppressing type comparison.
## Commands you will need
| Purpose | Command | Expected on success |
|---|---|---|
| Artifact tests | <code>uv run pytest -q tests/test_deployment_artifacts.py tests/test_cli.py</code> | all pass |
| Local migration upgrade | <code>uv run alembic upgrade head</code> | exit 0 |
| Local drift check | <code>uv run alembic check</code> | exit 0 and reports no new upgrade operations |
| Format/lint/type | <code>uv run ruff format --check .; uv run ruff check .; uv run ty check</code> | each exits 0 |
| Full tests | <code>uv run pytest -q</code> | all offline tests pass |
Run migration commands against a disposable SQLite DB through DB_URL, never a real operator database.
## Scope
**In scope**:
- .github/workflows/ci.yml
- tests/test_deployment_artifacts.py
- tests/test_cli.py
- alembic/versions/0007_align_telegram_identifier_types.py
- docs/testing.md
- PLAN.md
- alembic/env.py only if comparison configuration requires a narrowly documented adjustment
**Out of scope**:
- Creating a schema revision without an actual model change
- Changing migration history or squashing revisions
- Adding mypy or replacing ty
- Git mutations
## Git workflow
- Read-only git inspection only; no stage, commit, push, branch, reset, or worktree.
## Steps
### Step 1: Give CI migration checks an isolated database
Configure the migration step with an explicit disposable SQLite DB_URL under the CI workspace or runner temp directory. Run upgrade head and alembic check sequentially against the same database. Preserve the existing application startup and Docker checks.
**Verify**: workflow syntax remains valid and both exact commands appear in the migration step.
### Step 2: Update artifact regression coverage
Extend test_github_actions_ci_runs_quality_migration_and_docker_gates to require uv run alembic check and the isolated DB_URL. Do not merely search for the word check; assert the executable command.
**Verify**: tests/test_deployment_artifacts.py passes.
### Step 3: Exercise the gate locally
Set DB_URL to a disposable temp path, run upgrade head, then check. The expected Alembic message is that no new upgrade operations are detected. Delete only the explicitly named temp database through a safe operator-approved action; do not add cleanup code to the repository.
**Verify**: both commands exit 0.
### Step 4: Document and roadmap the gate
Update docs/testing.md CI description and exact local reproduction commands. Update PLAN.md migration gate, test coverage, version/date, and changelog without claiming hosted CI passed unless it actually did.
**Verify**: <code>rg -n "alembic check|metadata drift|Migration check" .github/workflows/ci.yml docs/testing.md PLAN.md tests/test_deployment_artifacts.py</code> → all surfaces agree.
### Step 5: Run all gates
Run artifact/CLI tests, format, lint, ty, and full tests.
**Verify**: all commands exit 0 and only in-scope files are modified.
## Test plan
- Artifact test requires both upgrade and check.
- Local disposable DB reaches head and reports no metadata operations.
- Do not create a fake drift inside committed source merely to test failure; the CI command itself is the guard.
## Done criteria
- [x] CI runs alembic upgrade head and alembic check against the same disposable DB.
- [x] Artifact tests enforce both commands.
- [x] Local drift check exits 0 at current head.
- [x] Testing docs and PLAN describe the exact gate honestly.
- [x] All quality gates pass.
## STOP conditions
- alembic check reports real drift at commit beda837; stop and report the specific metadata difference instead of suppressing it.
- The check requires compare-type/default exclusions that would hide legitimate schema changes.
- Only a production/operator DB is available for verification.
## Maintenance notes
- Every later model/migration plan should run this gate before being marked DONE.
- Do not normalize away SQLite differences without documenting exactly why they are semantically equivalent.
