# THOUGHTS — Things Heard, Observed, Unclear, Guessed, Hacked, Tracked, or Suspected : A living document about non-trivial details

This document will serve as a scratchpad for things encountered during development.  
You will find here notes about undocumented stuff, questions about implementation, random ideas that don't fit quite yet the PLAN, ...  
Developpers and Agents alike can write here freely so knowledge never gets lost. Some things can stay in a conversation, in an LLM inner Chain-of-Thoughts, in reasoning, ... but what if we start a new thread ? Everything gets lost. `THOUGHTS.md` fixes that, by providing a persistent, searchable record of non-trivial details that Agents and Developpers can write into and refer to.  
Information already present elsewhere (README, AGENTS, PLAN, PROGRESS, plans folder, docs, code & comments, ...) shouldn't be duplicated here.

## 2026-07-29 — GPT-5 — Codex Desktop — "Implementation roadmap execution"

- Ruff 0.16.0 can panic with `Expected a ruff source file` when workspace-local pytest base-temp trees are present; keep pytest temporary directories outside the repository and remove only tool-created in-repo temp trees before retrying Ruff.
- Give every pytest run a fresh external `--basetemp` and do not launch a replacement while a prior run may still be alive; reusing a directory locked by an orphaned `aiosqlite` process produced more than 100 misleading Windows `PermissionError` failures with no application assertion failure.
- The Plan 011 retrieval pass reduced 1,000/10,000-candidate hydration from 1,001/10,001 SQL queries to four, with a 5,000-candidate default that prioritizes durable knowledge before recent raw messages; preserve that tiering and query-count invariant while finishing roadmap reconciliation and full-suite verification.
- Alembic reads the database override from `DB_URL`, not `DATABASE_URL`; using the latter is silently ignored and can migrate the default local database instead of the intended disposable acceptance database, so set and verify `DB_URL` before migration smoke tests.
- In `web/`, run package scripts as raw `pnpm` commands: RTK rewrites `pnpm typecheck` into its `tsc` shortcut, and this command host may not resolve `pnpm exec`; use `pnpm typecheck` and `pnpm <bin>` (for example `pnpm playwright ...`) so pnpm injects the managed Node runtime.
- On the Windows Codex host used for the Phase 4 checkpoint, both raw and RTK-wrapped `pnpm playwright test` failed to resolve the installed local binary while the declared `pnpm test:e2e` package script worked; prefer that script for the repository Playwright gate.
- Plan 021 TASK-016 needs `PyJWT[crypto]` during Phase 3 even though TASK-024 originally places it in Phase 4; install only the JWT/cryptography dependencies in Phase 3 and keep provider-vault preflight and key-pool encryption in Phase 4.
- Telegram's live OIDC discovery currently advertises multiple signing algorithms and the Mini App docs now publish `telegram-web-app.js?63`; keep the deployment pinned to verified `RS256` and update the frontend script from the Phase 2 `?61` snapshot rather than trusting discovery algorithms dynamically.
- Phase 3 must encrypt one-time OIDC PKCE verifiers before the general provider vault exists; use an auth-specific encryption boundary derived from the OIDC bootstrap secret with transaction/redirect-bound authenticated data, without pulling Phase 4's provider-vault abstraction forward.
