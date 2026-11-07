# Telegram Export Import Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Support bounded HTML-only and JSON Telegram Desktop imports without guessing forum-topic ownership.  
**Architecture:** Format-specific streaming readers feed one shared embedding/write pipeline. Keep the existing JSON facade/command compatible, introduce automatic file/directory selection and an HTML alias. Topic targets are explicit, whole-forum archives remain aggregate chat memory.  
**Tech Stack:** Existing Python 3.12+, stdlib HTMLParser, ijson, SQLAlchemy and pytest, no new dependency.  
**Spec:** [Approved design](../specs/2026-11-07-telegram-export-import-design.md).  
**Status:** Completed and deployed for the approved narrow scope, exact source/image/test/live evidence and unrelated release gates are recorded in [the current acceptance report](../../mixed-provider-acceptance-2026-11-07.md).  
## Global Constraints
- Preserve the owner's Git index and all unrelated changes, no Git mutation/worktree/commit
- Original exports remain in Downloads, no JavaScript execution, network resource fetching or source-archive modification
- Keep 512 MiB aggregate input and 100,000-message limits, 100-row independently audited write batches and provider calls outside transactions
- HTML cannot invent missing numeric Telegram user IDs or infer a topic from arbitrary reply links
- DeepSeek, inference-server installation and unrelated privacy/permission changes remain excluded
## Review Focus
- Joined HTML author blocks must inherit the preceding author without accidentally copying a forwarded author
- Pagination must not traverse outside the selected directory, follow symlinks, fetch URLs or loop
- Both-format archives must not import twice, JSON then HTML retries must recognize the same logical IDs
- An explicit topic target must not accept contradictory explicit source-thread metadata
- General-topic updates must not retrieve aggregate chat or sibling-topic content, explicit aggregate queries must still contain every topic
## Task 1: General-topic boundary correction
**Files:** `src/pzp_ai_bot/telegram/utils.py`, `tests/test_scope.py`, `tests/test_telegram_integration.py`.  
**Interfaces:** `scope_from_message(message) -> Scope` maps a forum message without a topic flag to `Scope(chat_id, 1)`, `parse_scope("this_chat")` remains aggregate.  
- [x] Write failing unit/dispatcher regressions with literal General/News/private scopes and prove sibling markers appear before the fix (4 expected failures)
- [x] Apply the minimal transport-boundary normalization and rerun the same regressions (7 passed)
- [x] Run the full scope/Telegram integration slice, then repeat the live synthetic General negative check after deployment (104 focused passes, fresh topic-1 canary and deployed positive/negative checks passed)
## Task 2: Safe streaming HTML reader
**Files:** create `src/pzp_ai_bot/memory/telegram_html.py` and `tests/test_telegram_html.py`.  
**Interfaces:** `iter_telegram_html(path: Path)` yields normalized typed message rows with ID, author label, timezone-aware timestamp, content, reply ID and chat title, preserving unknown numeric user IDs as None.  
- [x] Add failing real-shape tests for text/entities/links/attachments, service-row exclusion, joined/forwarded authors and timezone offsets
- [x] Add failing pagination/path/symlink/cycle/malformed/row-byte-limit tests
- [x] Implement the stdlib HTMLParser reader, ignoring active content and following only validated same-directory export pagination
- [x] Run `uv run --no-sync pytest tests/test_telegram_html.py -q` with a fresh external basetemp and require ordinary passes
## Task 3: Shared import pipeline and commands
**Files:** `src/pzp_ai_bot/memory/transfer.py`, `memory/service.py`, `main.py`, `tests/test_product_controls.py`, `tests/test_cli.py`, `tests/test_architecture_facades.py`.  
**Interfaces:** add `MemoryService.import_telegram_export(path: Path, scope: Scope) -> TelegramDesktopImportResult`, keep `import_telegram_desktop_json`, add CLI `import-telegram` and `import-telegram-html` beside the existing JSON command. Directory selection prefers result.json over messages.html.  
- [x] Add failing JSON/HTML idempotency, explicit topic isolation, aggregate-only forum import, contradictory thread metadata, content-free audit and provider-outside-transaction tests
- [x] Extract only the existing bounded persistence loop needed by both readers, keep source hashes/exact-space routing and JSON event names compatible, add HTML-specific checkpoint events
- [x] Wire CLI/facade inventories and actionable format/scope warnings, synchronize command/operations/setup/testing docs and PLAN
- [x] Run focused transfer/CLI/architecture regressions, Ruff format/lint and ty, require passes without relaxing behavioral assertions
## Task 4: Combined verification and live acceptance
**Files:** canonical live evidence and PLAN, ignored acceptance scripts/artifacts.  
- [x] Parse the actual whole-group Both and individual-topic Both files in disposable fake-provider databases, compare IDs/text/scope/timestamps and prove repeated cross-format imports add no duplicates (13/13 whole-forum and 4/4 topic import/skip in both orders)
- [x] Run the complete Python suite with a fresh external basetemp, record all warnings/skips/failures, verify lock and API drift proportionately (602 passed, one live skip, three known warnings, final corrected-source gate)
- [x] Build/deploy only changed application services behind the same URL with the existing backup/tunnel retained, check health and browser-session persistence
- [x] Repeat ordinary-group search after its recorded transient Telegram send failure, verify Ask/summary, General/News/private positive and negative scope checks, explicit aggregate retrieval and current embedding freshness (24/24 current sources, zero missing/stale, 13 legacy vectors retained)
- [x] Reconcile design/plan/roadmap/live report, save proof screenshots and hand over qualified results, Git changes remain user-owned
## Review and execution
Native execution is recommended because the reader, transfer loop and facade tests share a small contract and the existing live UI session is owned by the main agent. No commits are part of this plan.  
The native pass is complete, a fresh read-only scoped review found one Important/P2 forwarded-body content-loss issue and its regression went RED→GREEN before the final full suite and backend build/deployment. Rulings: retain the existing ignored execution-ledger location instead of Git-managing helpers (less automation, no product difference), keep focused cross-format tests in their own discovered module (full pytest pins discovery), require operator-controlled immutable archives rather than solving adversarial file replacement races (changing sources invalidates provenance), and keep unrelated staged implementation and unexecuted release matrices outside this review (their separate gates remain open). No deferred minor finding was returned.  
