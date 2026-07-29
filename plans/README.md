# Improvement plans
This directory turns the repository survey performed on 2026-07-13 into executor-ready implementation plans. Every finding that was reported to the user has its own plan, and every plan was written against commit `beda837`.
## How to use this index
Read a plan completely before editing, run its drift check first, and leave its status as `TODO` until every done criterion is satisfied. `PARTIAL` means implementation has landed but a listed verification, documentation, migration, or release gate remains; `DONE` means the plan is fully implemented and verified. Plans deliberately contain no authorization for commits, staging, pushes, worktrees, or other git mutations.
## Plan index
| Plan | Audit finding | Title | Priority | Effort | Depends on | Status |
| --- | ---: | --- | --- | --- | --- | --- |
| [001](001-authorize-review-decisions-by-scope.md) | 1 | Authorize every review decision against the review's owning scope | P1 | S | None | DONE |
| [002](002-rebuild-markdown-from-committed-memory.md) | 2 | Make Markdown a privacy-correct projection of committed memory | P1 | L | None | DONE |
| [003](003-keep-provider-failures-retryable.md) | 3 | Keep summarization failures retryable without persisting raw chat | P1 | M | None | DONE |
| [004](004-supervise-background-runtime-tasks.md) | 4 | Keep background loops alive and make health reflect their state | P1 | M | None | DONE |
| [005](005-validate-allow-admins-input.md) | 5 | Reject invalid allow-admins input without changing consent | P1 | S | None | DONE |
| [006](006-gate-alembic-model-drift.md) | 6 | Fail CI when SQLAlchemy metadata drifts from Alembic history | P2 | S | None | DONE |
| [007](007-enforce-concurrent-message-idempotency.md) | 7 | Enforce logical message uniqueness under concurrent SQLite writes | P1 | M | 006 | DONE |
| [008](008-bound-and-claim-ai-work.md) | 8 | Bound AI batches, claim summary work atomically, and shorten database transactions | P1 | L | 002, 003, 006, 007 | DONE |
| [009](009-remove-unused-cross-scope-learning-permission.md) | 9 | Remove advertised but unused cross-scope learning permission | P2 | M | 006 | DONE |
| [010](010-persist-provider-failure-telemetry.md) | 10 | Persist provider failure telemetry after business rollback | P2 | M | 008 | DONE |
| [011](011-bound-and-batch-retrieval.md) | 11 | Batch retrieval hydration and enforce a measured candidate bound | P2 | L | None | DONE |
| [012](012-compact-only-dirty-scopes.md) | 12 | Canonicalize only dirty scopes and reuse committed snapshots | P2 | L | 002, 006, 008, 010 | DONE |
| [013](013-make-lockfile-consumption-reproducible.md) | 13 | Make CI and Docker consume the uv lockfile immutably | P2 | S | None | DONE |
| [014](014-reconcile-living-roadmap.md) | 14 | Reconcile `PLAN.md` with current repository and release gates | P2 | S | None | TODO |
| [015](015-complete-cli-command-reference.md) | 15 | Make the canonical CLI reference complete and self-checking | P3 | S | None | DONE |
| [016](016-enforce-coverage-regression-signal.md) | 16 | Turn existing coverage tooling into a conservative CI regression gate | P3 | S | None | DONE |
| [017](017-load-policy-snapshots-once.md) | Additional A | Evaluate learning policy from one immutable snapshot per operation | P3 | S | None | DONE |
| [018](018-aggregate-and-index-telemetry.md) | Additional B | Normalize telemetry scope, aggregate in SQL, and index audit queries | P3 | M | 006, 010 | DONE |
| [019](019-stream-and-batch-bulk-transfer.md) | Additional C | Stream and batch memory imports and exports | P3 | L | 008, 013 | DONE |
| [020](020-split-hotspots-behind-compatible-facades.md) | Additional D | Split service, repository, and router hotspots behind compatible facades | P3 | L | 001-005, 007-012, 017-019 | PARTIAL |
## Recommended execution order
Wave A contains the independent safety fixes, gates, documentation repairs, and low-risk efficiency work: 001, 002, 003, 004, 005, 006, 011, 013, 015, 016, and 017. Plan 014 is also independent, but it is usually cheaper to execute it after an implementation batch so `PLAN.md` is reconciled once against the resulting state.
Wave B starts after the migration drift gate exists: 007 and 009.
Wave C repairs the transactional summarization pipeline after its prerequisites: 008.
Wave D adds durable failure accounting and scalable bulk transfer after the pipeline and lockfile foundations: 010 and 019.
Wave E builds incremental maintenance and queryable telemetry on the stabilized persistence model: 012 and 018.
Wave F performs structural extraction only after the behavioral changes have settled: 020. This avoids moving defects or creating merge-heavy duplicate edits while the service, repository, and router behavior is still changing.
## Global execution rules
- Preserve existing user changes and begin with read-only worktree inspection.
- Use the project `.venv` and uv-managed Python; do not fall back to an outdated system Python. If the WinGet shim is broken, invoke uv from `%LOCALAPPDATA%\Microsoft\WinGet\Packages`.
- Do not stage, commit, push, reset, switch branches, or create worktrees. Git use in these plans is read-only.
- Keep `PLAN.md` aligned after every meaningful implementation pass, with `DONE`, `PARTIAL`, and `TODO` states kept distinct.
- Keep canonical user-facing behavior in `README.md` and the relevant `docs/*.md` file instead of duplicating it in `AGENTS.md`.
- Use `build_context(Settings)` for production application wiring, `session_scope(...)` and repositories for service database access, and the shared `context` fixture for offline deterministic tests.
- Treat schema work as a three-surface change: SQLAlchemy models, an Alembic revision, and focused upgrade/runtime tests.
- Keep Telegram handlers, command descriptions, help text, tests, and `docs/commands.md` synchronized when command behavior changes.
- Use `ty`; do not introduce mypy.
- Keep default tests offline, and retain real Telegram checks behind the existing `telegram_live` marker.
## Survey verification baseline
At the audited commit, Ruff formatting, Ruff linting, and `ty` passed; pytest reported 138 passed and 1 skipped; the measured coverage result was 79%. These are historical baselines for drift detection, not substitutes for each plan's targeted and full verification after implementation.
## Assessed items not turned into findings
- SQLite plus Markdown remains an explicit single-node v1 design choice, so the survey did not recommend replacing the persistence architecture.
- Scoped retrieval-augmented generation rather than fine-tuning remains an explicit product boundary, so it was not treated as missing functionality.
- The fake provider and offline test defaults are deliberate determinism controls, not production provider defects.
- The optional live Telegram test is a release-environment gate and future validation direction, not a default-suite defect.
- `ty` is the repository's intentional type checker; adding mypy would conflict with project policy.
- No pre-commit or `.editorconfig` finding was raised because the repository already has meaningful automated quality gates and this survey found no evidence that another local hook layer would solve a demonstrated defect.
- uv caching was not raised because the workflow already enables setup-uv caching.
- Dependency freshness and vulnerability status were not claimed because the survey did not perform a full online dependency advisory audit.
- Phase-named Alembic revisions are historical identifiers, not evidence of stale migration state.
- A static suspicion around currently unused `numpy`, `orjson`, or the `httpx` extra was not promoted to a finding because safe removal requires a clean-lock, import, test, and packaging experiment.
## Direction ideas intentionally outside this backlog
The survey also offered three product directions: a disposable-group Gate C end-to-end harness, a preview-and-confirm import workflow, and a provider/model quality-cost replay benchmark. They are choices that require product or operational acceptance criteria, not confirmed defects, so they are intentionally not disguised as findings in these 20 plans.
