# Plan 015: Make the canonical CLI reference complete and self-checking
> **Executor instructions**: Follow each step and gate, keep docs as canonical human-facing material, and update plans/README.md when complete unless a reviewer owns it.
> **Drift check (run first)**: <code>git diff --stat beda837..HEAD -- src/pzp_ai_bot/main.py docs/commands.md tests/test_deployment_artifacts.py tests/test_cli.py PLAN.md</code>.
## Status
- **Status**: DONE
- **Audit finding**: 15
- **Priority**: P3
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none
- **Category**: docs
- **Planned at**: commit beda837, 2026-07-13
## Why this matters
README.md directs operators to docs/commands.md as the command reference, but its CLI section omits migrate, polling, webhook, summarize-now, export-memory, and import-jsonl. These include core startup, migration, and data-lifecycle operations. Document every registered Typer command and add an inventory test so future additions require an intentional documentation decision.
## Current state
- docs/commands.md:64-75 documents doctor, show-config, memory-status, import-telegram-json, audit-events, runtime-settings, and DB commands.
- main.py:50-134 also registers migrate, polling, webhook, summarize-now, export-memory, and import-jsonl.
- tests/test_cli.py:178-205 exercises successful summarize-now/import-jsonl/export-memory.
- tests/test_deployment_artifacts.py is the existing documentation/artifact inventory home.
## Commands you will need
| Purpose | Command | Expected on success |
|---|---|---|
| CLI/artifact tests | <code>uv run pytest -q tests/test_cli.py tests/test_deployment_artifacts.py</code> | all pass |
| CLI help | <code>uv run pzp-bot --help</code> | exit 0 and lists registered commands |
| Format/lint/type | <code>uv run ruff format --check .; uv run ruff check .; uv run ty check</code> | each exits 0 |
| Full tests | <code>uv run pytest -q</code> | all offline tests pass |
## Implementation result
The live Typer inventory regression first reported exactly `export-memory`, `import-jsonl`, `migrate`, `polling`, `summarize-now` and `webhook` as missing. The CLI reference now documents all six with their principal options, mutating/long-running/provider/sensitive-data cautions and links to canonical guidance. `uv run pzp-bot --help`, the 14-test CLI/artifact target, Ruff format/lint, `ty` and the complete 180-passed/1-skipped offline suite passed on 2026-07-29.
## Scope
**In scope**:
- docs/commands.md
- tests/test_deployment_artifacts.py
- tests/test_cli.py only if command-help behavior needs coverage
- PLAN.md
**Out of scope**:
- Renaming or changing CLI commands
- Duplicating setup/deployment details already canonical in their own docs
- Executing polling, webhook, or destructive DB commands during tests
- Git mutations
## Git workflow
- Read-only git inspection only; no staging, commits, pushes, branches, resets, or worktrees.
## Steps
### Step 1: Inventory commands from the live Typer app
Use typer.main.get_command(app).commands or another stable Typer/Click public surface in a test to enumerate registered names. Compare that list to the CLI section, not to a second handwritten source list.
**Verify**: the test initially identifies the six documented omissions.
### Step 2: Document every missing command
Add concise entries for migrate, polling, webhook, summarize-now, export-memory, and import-jsonl with principal options and operational cautions. State that polling/webhook are long-running, migrate changes schema, summarize-now may invoke a provider, export can write/stdout sensitive memory, and import-jsonl writes durable scoped memory. Link to canonical setup/deployment/privacy pages rather than duplicating them.
**Verify**: <code>rg -n "pzp-bot (migrate|polling|webhook|summarize-now|export-memory|import-jsonl)" docs/commands.md</code> → six entries exist.
### Step 3: Add a documentation inventory regression
In tests/test_deployment_artifacts.py, enumerate live Typer commands and assert docs/commands.md contains a backticked pzp-bot command token for each. Make failure output name missing commands. Avoid parsing rendered Rich help text.
**Verify**: targeted artifact tests pass.
### Step 4: Update PLAN
Record complete CLI reference coverage and the inventory test in PLAN.md docs/test map/version/date/changelog. Do not claim command behavior changed.
**Verify**: <code>rg -n "CLI reference|command inventory" PLAN.md</code> → documentation work is recorded.
### Step 5: Run all gates
Run CLI help, targeted tests, format, lint, ty, and full tests.
**Verify**: all exit 0 and only in-scope files are modified.
## Test plan
- Every live Typer command has a docs token.
- Test failure names omitted command(s).
- No long-running or destructive command is executed.
- Existing CLI behavior tests remain unchanged and pass.
## Done criteria
- [x] All registered commands appear in docs/commands.md.
- [x] Missing future commands fail a stable inventory test.
- [x] Operational cautions and canonical-doc links are present without duplication.
- [x] PLAN and all gates pass.
## STOP conditions
- Typer's inspected command API is unstable in the pinned version and requires private internals likely to break; use a small explicit exported registry in main.py only with a separately reviewed source change.
- A command's behavior cannot be documented from source/tests without executing a destructive path.
## Maintenance notes
- When commands are added or renamed, update implementation, help, docs, tests, and PLAN in the same pass.
