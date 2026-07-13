# Plan 013: Make CI and Docker consume the uv lockfile immutably
> **Executor instructions**: Follow each step and gate, use the authorized uv runtime rather than system Python, and update plans/README.md when complete unless a reviewer owns it.
> **Drift check (run first)**: <code>git diff --stat beda837..HEAD -- pyproject.toml uv.lock Dockerfile .github/workflows/ci.yml tests/test_deployment_artifacts.py docs/deployment.md docs/testing.md PLAN.md</code>.
## Status
- **Audit finding**: 12
- **Priority**: P2
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none
- **Category**: dx
- **Planned at**: commit beda837, 2026-07-13
## Why this matters
The repository has uv.lock, but Docker copies only pyproject.toml and resolves with uv pip install, while CI runs uv sync without frozen mode. Docker also installs an unpinned uv release. A single commit can therefore resolve different build tooling or dependency sets over time. CI and Docker should fail on lock drift and use one explicitly pinned uv version.
## Current state
- uv.lock is committed.
- Dockerfile:9 runs pip install --no-cache-dir uv without a version.
- Dockerfile:11-13 omits uv.lock and uses uv pip install . rather than lock-aware sync.
- ci.yml:31-32 runs uv sync --extra dev without --frozen.
- tests/test_deployment_artifacts.py asserts the current mutable command strings.
Current Docker layer:
    RUN pip install --no-cache-dir uv
    COPY pyproject.toml README.md ./
    COPY src ./src
    RUN uv venv /opt/venv && uv pip install --python /opt/venv/bin/python .
## Commands you will need
| Purpose | Command | Expected on success |
|---|---|---|
| Show authorized uv | <code>uv --version</code> or the operator-approved WinGet uv path | prints one version; record it |
| Lock validation | <code>uv lock --check</code> | exit 0; lock is current |
| Frozen dev sync | <code>uv sync --frozen --extra dev</code> | exit 0 without changing uv.lock |
| Artifact tests | <code>.\.venv\Scripts\python.exe -m pytest -q tests/test_deployment_artifacts.py</code> | all pass |
| Format/lint/type | <code>.\.venv\Scripts\ruff.exe format --check .; .\.venv\Scripts\ruff.exe check .; .\.venv\Scripts\ty.exe check</code> | each exits 0 |
| Full tests | <code>.\.venv\Scripts\python.exe -m pytest -q</code> | all offline tests pass |
If uv cannot execute in the sandbox, stop and ask for the explicit WinGet executable authorization; do not use system Python or guess a tool version.
## Scope
**In scope**:
- pyproject.toml only if recording the chosen uv version there is supported by the live uv release
- uv.lock only if a legitimate lock refresh is required and explicitly reviewed
- Dockerfile
- .github/workflows/ci.yml
- tests/test_deployment_artifacts.py
- docs/deployment.md
- docs/testing.md
- PLAN.md
**Out of scope**:
- Dependency upgrades unrelated to making the current lock immutable
- Pinning the python:3.12-slim image digest; handle that under a separate base-image update policy
- Adding a dependency bot or registry credentials
- Git mutations
## Git workflow
- Read-only git inspection only; no stage, commit, push, branch, reset, or worktree.
## Steps
### Step 1: Record one uv version
Run the authorized uv executable and record its exact version in a single reviewed project location supported by the installed tooling, or define one explicit value used identically by Docker and setup-uv. Configure setup-uv to install that exact version and Docker to install uv==$UV_VERSION. Do not pin an unverified guessed version.
**Verify**: artifact tests parse both files and assert their uv version values are identical and non-empty.
### Step 2: Freeze CI resolution
Change dependency installation to uv sync --frozen --extra dev and optionally precede it with uv lock --check for a clearer error. The workflow must fail rather than update uv.lock when pyproject and lock disagree.
**Verify**: <code>rg -n "uv lock --check|uv sync --frozen --extra dev" .github/workflows/ci.yml</code> → immutable commands are present.
### Step 3: Make Docker lock-aware
Copy pyproject.toml, uv.lock, and README.md before source code. Configure UV_PROJECT_ENVIRONMENT=/opt/venv. Install locked production dependencies with uv sync --frozen --no-dev --no-install-project, copy src, then run uv sync --frozen --no-dev to install the project without resolving new versions. Preserve multi-stage, non-root, healthcheck, Alembic, and docs behavior.
**Verify**: Dockerfile contains uv.lock and --frozen, contains no unversioned pip install uv, and artifact tests pass.
### Step 4: Prove lock immutability
Record uv.lock hash or git diff before and after uv lock --check plus frozen sync; it must not change. If Docker is available, build the image twice from a clean cache policy and run pzp-bot doctor/migrate as documented. Do not claim Docker verification if unavailable.
**Verify**: <code>git diff -- uv.lock</code> → empty unless a separately justified lock repair was required.
### Step 5: Update docs and PLAN
Update deployment/testing docs with pinned tool and frozen commands. Update PLAN.md build/release gates, verification baseline, risks, date/version, and changelog; distinguish local artifact verification from a hosted CI run.
**Verify**: <code>rg -n "frozen|uv.lock|uv version" Dockerfile .github/workflows/ci.yml docs/deployment.md docs/testing.md PLAN.md</code> → all surfaces agree.
### Step 6: Run all gates
Run lock validation, artifact tests, format, lint, ty, and full tests.
**Verify**: all exit 0 and only in-scope files are modified.
## Test plan
- CI command is frozen and lock-checked.
- Docker copies uv.lock before source and uses frozen production sync.
- Docker and CI pin the same uv version.
- Mutable legacy strings are absent.
- Existing non-root/healthcheck/docker-smoke assertions remain.
## Done criteria
- [ ] CI fails on lock drift and cannot silently rewrite uv.lock.
- [ ] Docker installs exactly locked production dependencies.
- [ ] uv itself is pinned consistently.
- [ ] No unrelated dependency versions changed.
- [ ] Deployment/testing docs and PLAN are accurate.
- [ ] Artifact and full gates pass.
## STOP conditions
- The authorized uv version cannot be determined.
- Frozen sync exposes real pyproject/lock drift; report it rather than regenerating the lock opportunistically.
- The live uv release does not support the planned sync flags; consult its official docs and revise the plan explicitly.
- Docker verification would require registry credentials not already in scope.
## Maintenance notes
- Dependency upgrades should deliberately refresh uv.lock in their own change; ordinary CI/builds must remain frozen.
- Base-image digest pinning remains a separate supply-chain decision.
