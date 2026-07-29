# THOUGHTS — Things Heard, Observed, Unclear, Guessed, Hacked, Tracked, or Suspected : A living document about non-trivial details

This document will serve as a scratchpad for things encountered during development.  
You will find here notes about undocumented stuff, questions about implementation, random ideas that don't fit quite yet the PLAN, ...  
Developpers and Agents alike can write here freely so knowledge never gets lost. Some things can stay in a conversation, in an LLM inner Chain-of-Thoughts, in reasoning, ... but what if we start a new thread ? Everything gets lost. `THOUGHTS.md` fixes that, by providing a persistent, searchable record of non-trivial details that Agents and Developpers can write into and refer to.  
Information already present elsewhere (README, AGENTS, PLAN, PROGRESS, plans folder, docs, code & comments, ...) shouldn't be duplicated here.

## 2026-07-29 — GPT-5 — Codex Desktop — "Implementation roadmap execution"

- Ruff 0.16.0 can panic with `Expected a ruff source file` when workspace-local pytest base-temp trees are present; keep pytest temporary directories outside the repository and remove only tool-created in-repo temp trees before retrying Ruff.
- Give every pytest run a fresh external `--basetemp` and do not launch a replacement while a prior run may still be alive; reusing a directory locked by an orphaned `aiosqlite` process produced more than 100 misleading Windows `PermissionError` failures with no application assertion failure.
- The Plan 011 retrieval pass reduced 1,000/10,000-candidate hydration from 1,001/10,001 SQL queries to four, with a 5,000-candidate default that prioritizes durable knowledge before recent raw messages; preserve that tiering and query-count invariant while finishing roadmap reconciliation and full-suite verification.
