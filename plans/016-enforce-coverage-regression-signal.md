# Plan 016: Turn existing coverage tooling into a conservative CI regression gate
> **Executor instructions**: Follow each step and gate. Do not add low-value tests merely to raise a percentage. Update plans/README.md when complete unless a reviewer owns it.
> **Drift check (run first)**: <code>git diff --stat beda837..HEAD -- pyproject.toml .github/workflows/ci.yml tests/test_deployment_artifacts.py docs/testing.md PLAN.md</code>.
## Status
- **Status**: RETIRED on 2026-11-07 (owner-requested removal), historically implemented and verified on 2026-07-29
- **Audit finding**: 16
- **Priority**: P3
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none
- **Category**: tests
- **Planned at**: commit beda837, 2026-07-13
This file preserves the historical rationale, commands and results below. It is not an active execution plan: pytest-cov, coverage configuration, percentage thresholds and CI instrumentation have been removed, plain behavioral tests remain required. Do not reintroduce this gate while replaying an old execution wave.
## Why this matters
pytest-cov is installed and branch coverage is configured, but CI runs plain pytest. Coverage can regress without any signal. The audited baseline was 79 percent with branch measurement enabled; introduce a conservative 78-percent floor and visible missing-line report, while keeping critical-path behavior more important than maximizing the global number.
## Current state
- pyproject.toml:33 includes pytest-cov.
- pyproject.toml:74-76 enables branch coverage over pzp_ai_bot.
- ci.yml:43-44 runs uv run pytest without coverage.
- docs/testing.md:5-10 documents plain pytest.
- tests/test_deployment_artifacts.py:26-41 asserts the plain command.
## Commands you will need
| Purpose | Command | Expected on success |
|---|---|---|
| Measure baseline | <code>uv run pytest -q --cov=pzp_ai_bot --cov-branch --cov-report=term-missing</code> | tests pass; total remains at least 79 before threshold change |
| Artifact tests | <code>uv run pytest -q tests/test_deployment_artifacts.py</code> | all pass |
| Format/lint/type | <code>uv run ruff format --check .; uv run ruff check .; uv run ty check</code> | each exits 0 |
| Full coverage gate | <code>uv run pytest -q --cov=pzp_ai_bot --cov-branch --cov-report=term-missing</code> | tests pass and coverage is at least configured floor |
## Implementation result
The unmodified live baseline measured 80.72%, reported as 81%, across 5,037 statements and 1,442 branches. `pyproject.toml` now enforces `fail_under = 78` with missing lines visible, and CI runs the exact branch-coverage command. Seven focused artifact tests, Ruff format/lint, `ty` and the complete 180-passed/1-skipped coverage suite passed on 2026-07-29. A repository-unmodified `--cov-fail-under=82` proof ran the same passing tests but exited non-zero with `total of 81 is less than fail-under=82`, proving the signal trips above the measured baseline.
## Scope
**In scope**:
- pyproject.toml
- .github/workflows/ci.yml
- tests/test_deployment_artifacts.py
- docs/testing.md
- PLAN.md
**Out of scope**:
- New tests written solely to inflate coverage
- A third-party coverage SaaS, token, badge, or upload
- Raising the threshold above the measured baseline
- Changing telegram_live default exclusion
- Git mutations
## Git workflow
- Read-only git inspection only; no staging, commits, pushes, branches, resets, or worktrees.
## Steps
### Step 1: Re-measure before editing
Run the exact baseline command. If total coverage is below 79 at the live commit, stop and investigate drift instead of choosing a lower threshold silently. Record uncovered critical paths separately; this plan adds the signal, not broad test implementation.
**Verify**: report is captured in terminal and total is at least 79.
### Step 2: Configure a conservative floor
Add tool.coverage.report configuration with fail_under = 78 and show_missing = true. Keep branch = true and source unchanged. Do not add omit rules for low-coverage production modules.
**Verify**: rerun the coverage command and confirm exit 0.
### Step 3: Make CI collect coverage
Replace the plain pytest CI command with uv run pytest --cov=pzp_ai_bot --cov-branch --cov-report=term-missing. Keep default tests offline and telegram_live skipped. Do not generate an unconsumed XML artifact.
**Verify**: workflow contains the exact coverage command and no plain duplicate test step.
### Step 4: Update artifact test and testing docs
Require the coverage command in test_deployment_artifacts.py. Document local use, the 78 floor, the measured starting baseline, branch coverage, and the policy that behavior-critical tests outrank percentage chasing.
**Verify**: artifact test passes and docs contain baseline/floor distinctions.
### Step 5: Update PLAN
Record coverage signal, measured baseline, current test gap notes, release gate, version/date, and changelog. Do not mark known critical test gaps complete merely because global coverage passes.
**Verify**: <code>rg -n "coverage|79|78|branch" pyproject.toml .github/workflows/ci.yml docs/testing.md PLAN.md</code> → configuration and docs agree.
### Step 6: Run all gates
Run artifact tests, format, lint, ty, and the full coverage gate.
**Verify**: all exit 0 and only in-scope files are modified.
## Test plan
- Artifact test enforces the coverage-enabled command.
- Coverage floor passes at current baseline.
- A local temporary threshold above measured total proves pytest-cov exits non-zero, but do not commit that temporary value.
- telegram_live remains skipped by default.
## Done criteria
- [x] CI collects branch coverage and shows missing lines.
- [x] A conservative 78-percent floor prevents major silent regression.
- [x] No production modules are omitted to game the number.
- [x] Docs and PLAN distinguish baseline, floor, and critical-path quality.
- [x] All gates pass.
## STOP conditions
- Fresh measured coverage is below 79 before changes.
- Enabling coverage changes test behavior or external-network isolation.
- The proposed threshold requires superficial tests or omission rules.
## Maintenance notes
- Raise the floor only after meaningful tests land and the new stable baseline is measured.
- Reviewers should still prioritize auth, privacy, destructive operations, migrations, and concurrency over global percentage.
