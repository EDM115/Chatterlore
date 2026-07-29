# Plan 005: Reject invalid allow-admins input without changing consent
> **Executor instructions**: Execute each step and gate, then update plans/README.md unless a reviewer owns it.
> **Drift check (run first)**: <code>git diff --stat beda837..HEAD -- src/pzp_ai_bot/telegram/router.py tests/test_telegram_integration.py docs/commands.md PLAN.md</code>.
## Status
- **Implementation status**: DONE (2026-07-29)
- **Audit finding**: 7
- **Priority**: P1
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none
- **Category**: bug
- **Planned at**: commit beda837, 2026-07-13
## Why this matters
The documented command accepts on or off, but every missing or unrecognized value is currently interpreted as false and persisted. A typo therefore disables learning consent instead of preserving state and returning a usage error. The router already has a strict shared boolean parser; this handler must use it and prove no mutation occurs on invalid input.
## Current state
- router.py:405-415 computes enabled with membership in the truthy set, so all other input means false.
- router.py:986-992 provides _parse_on_off, accepting explicit true/false forms and raising ValueError otherwise.
- docs/commands.md:19 documents /allow_admins on|off.
- tests/test_telegram_integration.py:465-505 supplies valid command examples but has no invalid-input non-mutation case.
Current code:
    arg = (command.args or "").strip().lower()
    enabled = arg in {"on", "true", "1", "yes"}
Existing parser:
    def _parse_on_off(raw: str) -> bool:
        ...
        raise ValueError(f"invalid boolean: {raw}")
## Commands you will need
| Purpose | Command | Expected on success |
|---|---|---|
| Telegram tests | <code>.\.venv\Scripts\python.exe -m pytest -q tests/test_telegram_integration.py</code> | all pass |
| Format/lint/type | <code>.\.venv\Scripts\ruff.exe format --check .; .\.venv\Scripts\ruff.exe check .; .\.venv\Scripts\ty.exe check</code> | each exits 0 |
| Full tests | <code>.\.venv\Scripts\python.exe -m pytest -q</code> | all offline tests pass |
## Scope
**In scope**:
- src/pzp_ai_bot/telegram/router.py
- tests/test_telegram_integration.py
- docs/commands.md only if accepted aliases or error wording changes
- PLAN.md
**Out of scope**:
- Changing policy persistence or accepted boolean vocabulary globally
- A new parser parallel to _parse_on_off
- Git mutation
## Git workflow
- Read-only git inspection only; no stage, commit, push, branch, reset, or worktree.
## Steps
### Step 1: Use the strict parser
Replace the truthy-membership expression with _parse_on_off(command.args or ""). Catch ValueError in the handler, answer with safe_answer using a concise usage message, and return before set_allow_admins. Keep require_admin first.
**Verify**: <code>.\.venv\Scripts\python.exe -m pytest -q tests/test_telegram_integration.py -k allow_admins</code> → selected tests pass.
### Step 2: Add non-mutation tests
Parameterize missing input, a typo such as onn, and unrelated text. Seed or represent an existing enabled state, feed each command, assert RouterMemoryDouble recorded no set_allow_admins call, and assert a usage/invalid-boolean response. Retain tests for on and off.
**Verify**: full test_telegram_integration.py passes.
### Step 3: Update PLAN
Record the corrected consent parsing and regression coverage in PLAN.md, preserving accurate DONE/PARTIAL/TODO state and changelog/version/date discipline. docs/commands.md already states the strict contract; change it only if needed for accepted aliases.
**Verify**: <code>rg -n "allow_admins|consent|invalid" PLAN.md docs/commands.md</code> → contract is consistent.
### Step 4: Run all gates
Run the commands above.
**Verify**: all exit 0 and only in-scope files are modified.
## Test plan
- Invalid and missing values never call set_allow_admins.
- Explicit on and off both still mutate once.
- Unauthorized users remain rejected before parsing.
- Error responses use safe_answer and do not echo secrets.
## Done criteria
- [x] Invalid input cannot change policy.
- [x] The existing strict parser is reused.
- [x] on/off and authorization behavior remain intact.
- [x] PLAN and any changed command docs are synchronized.
- [x] Targeted and full gates pass.

Verification on 2026-07-29 used `uv run`: missing, mistyped and unrelated values were rejected without mutation, explicit on/off values each mutated once, unauthorized users were rejected before parsing, the complete Telegram integration suite passed, Ruff format and lint passed, `ty check` passed, and the full offline pytest suite passed.
## STOP conditions
- _parse_on_off has changed to permissive fallback behavior.
- A fix requires changing all boolean command semantics without a separate plan.
- Tests cannot observe whether mutation occurred.
## Maintenance notes
- Consent-bearing booleans must fail closed on malformed input; future handlers should reuse the same strict parser.
