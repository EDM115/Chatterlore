# THOUGHTS — Things Heard, Observed, Unclear, Guessed, Hacked, Tracked, or Suspected : A living document about non-trivial details

This document will serve as a scratchpad for things encountered during development.  
You will find here notes about undocumented stuff, questions about implementation, random ideas that don't fit quite yet the PLAN, ...  
Developpers and Agents alike can write here freely so knowledge never gets lost. Some things can stay in a conversation, in an LLM inner Chain-of-Thoughts, in reasoning, ... but what if we start a new thread ? Everything gets lost. `THOUGHTS.md` fixes that, by providing a persistent, searchable record of non-trivial details that Agents and Developpers can write into and refer to.  
Information already present elsewhere (README, AGENTS, PLAN, PROGRESS, plans folder, docs, code & comments, ...) shouldn't be duplicated here.

## 2026-07-29 — GPT-5 — Codex Desktop — "Implementation roadmap execution"

- Use a fresh external pytest `--basetemp` for each run on Windows, reusing a directory locked by an orphaned `aiosqlite` process produced misleading `PermissionError` failures, do not start a replacement run while the previous process may still hold it
- Historical tool caveat (not revalidated with the current Ruff): Ruff 0.16.0 panicked with `Expected a ruff source file` when pytest temporary trees lived inside the workspace, keep those trees outside the repository rather than deleting unrelated work to recover
- The Windows development launcher uses persistent PowerShell 7 wrappers because direct `.cmd`/`uv.exe` children could exit while descendants retained inherited pipes, preserve whole-process-tree cleanup when changing `web/scripts/dev.mjs`

## 2026-08-09 — GPT-5 — Codex Desktop — "Plan 021 Phase 10 live acceptance checkpoint"

- The OpenAI SDK's test transport uses `httpx2`, the application's other HTTP integrations use `httpx`, keep exception/mock-transport fixtures aligned with the SDK rather than migrating unrelated clients (see `tests/test_ai_providers.py`)
- Switching Docker Desktop between WSL and Docker VMM changes the visible engine's containers/images/cache, it does not establish loss of host bind-mounted data, a VMM bind-mount sharing error was resolved through Docker Desktop Resources rather than recreating data
- Mini App step-up reuses the exact launch proof accepted at bootstrap in page memory because rereading the SDK value caused `step_up_encoding` even after a fresh successful login, preserve backend freshness/signature/same-identity checks and clear the proof with the session (see `web/app/composables/useControlSession.ts`)

## 2026-11-07 — Unknown model — Codex Desktop — "Audit implementation against plans"

- The owner's isolated deployment, backup/image provenance and import recovery points are recorded in ignored `data/deployment-20261107-mixed/OPERATOR-STATE.md` and `import-progress.md`, use the final checkpoint and recheck live processes/URLs before resuming, preserve original Downloads exports and distinguish `backup-before-0019` from `backup-before-forum-html`
- The later closure execution has separate evidence in ignored `data/release-closure-20261107/progress.md`, preserve those recovery artifacts, the process-bound tunnel, original groups and owner sessions during unrelated cleanup, the general `data` directory is not disposable test output
