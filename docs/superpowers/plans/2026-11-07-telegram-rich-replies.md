# Telegram Rich Replies Implementation Plan
> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.  
**Goal:** Format Telegram replies with safe rich content and real generation progress while keeping slash handlers usable without publishing a large menu.  
**Architecture:** Extend the existing safe-delivery and provider-operation boundaries, cumulative progress callbacks keep one shared RAG and telemetry path. Private chats use ephemeral rich drafts and final persistence, groups/topics use throttled single-message edits.  
**Tech Stack:** Existing Python, aiogram 3.31, OpenAI SDK, markdown-it-py 4.2.0 and pytest, only direct dependency metadata changes, no package graph upgrade or database change.  
**Spec:** [Design and boundaries](../specs/2026-11-07-telegram-rich-replies-design.md).  
## Constraints and review focus
- Preserve read-only Git ownership, current runtime/tunnel/data/secrets and offline default tests, no deployment or provider charge in this pass
- A rich network timeout must not trigger a duplicate final message, a definite format rejection may use a bounded literal fallback
- Model Markdown must not embed remote media or rich controls, application-owned dynamic values stay literal
- SSE retries may occur before content but cannot concatenate or silently replace a visible partial answer, cancellation closes the stream
- Private Stop updates must match exact chat/thread/draft, group traffic must never use private draft APIs
- Final source references and long Unicode content must survive formatting/fallback/chunking, a failed preview is not a failed AI request
## Task 1: Hide the application-published command menu
**Files:** `telegram/commands.py`, `polling.py`, `webhook.py`, `control_plane/development.py`, menu/startup/dispatcher tests.  
**Interfaces:** `clear_bot_commands(bot) -> None` deletes only the default list formerly published by this app, descriptions/help/handlers remain.  
- [x] Write a menu regression that fails because startup still publishes commands, require the real default delete method and no set method
- [x] Replace registration at all three startup boundaries, update their fake network contracts rather than removing dispatch coverage
- [x] Run menu/webhook/development/Telegram slices with a fresh external basetemp, require ordinary passes
## Task 2: Safe rich delivery and literal application formatting
**Files:** new `telegram/presentation.py`, extend `safe_api.py`, new `tests/test_telegram_presentation.py`, affected shared transport fixtures.  
**Interfaces:** `safe_answer(message,text,markdown=False,**kwargs) -> bool` delegates bounded rich delivery, model Markdown is explicit, application text uses literal blocks. Document sends remain compatible.  
- [x] Write failing serialized-request tests for emphasis/headings/code/tables, literal underscores/HTML, media/control neutralization, Unicode/length preservation and reply/thread/keyboard forwarding
- [x] Write failing rejection-fallback versus transport/rate-limit tests, assert no raw content/exception in logs
- [x] Implement bounded rich payload construction and one literal fallback only after a definite Telegram rejection, use clear non-AI headings where existing output already supplies a title
- [x] Run presentation and full Telegram slice, Ruff and ty, require passes
## Task 3: Real provider streaming through the current RAG/usage path
**Files:** `ai/protocols.py`, `openai_compatible.py`, `pooled_openai.py`, `memory/provider_operations.py`, `retrieval_service.py`, `service.py`, new `tests/test_chat_streaming.py`.  
**Interfaces:** optional `on_text: ChatProgressCallback | None` on Ask and provider execution, `generate_stream(...,on_text) -> ChatResult` on streaming-capable providers, callbacks receive cumulative visible content only.  
- [x] Write failing SSE tests proving incremental callbacks, input redaction, dialect budget parameters, final usage, reasoning exclusion and stream closure
- [x] Write pre-output retry and post-output interruption regressions, bounded response and cancellation cleanup/usage tests, include managed credential forwarding
- [x] Reuse the existing request builder and lease policy for full streamed consumption, preserve non-streaming provider compatibility and classify presentation failures separately
- [x] Run provider/RAG/credential regressions and static gates, require passes
## Task 4: Telegram progress, Stop isolation and final evidence
**Files:** new `telegram/streaming.py`, `telegram/routers/retrieval.py`, integration/architecture tests, canonical docs and PLAN.  
**Interfaces:** request-owned answer delivery with `update(text)`, `finish(text)` and exact draft Stop cancellation, one bounded registry per router.  
- [x] Write failing private-draft/final-persistence, exact Stop ownership, group/topic single-message edit/throttle, preview-failure isolation and final-source/long-answer regressions
- [x] Wire Telegram Ask to the optional progress callback, remove its 3900-character answer truncation, retain offline/nonstream compatibility and correct shutdown cancellation
- [x] Synchronize menu, formatting, streaming/fallback/privacy/operations docs and PLAN, do not claim complete Mini App feature parity
- [x] Run complete Python suite in fresh external basetemp, Ruff/ty/lock/API drift and focused final review, fix actionable findings with RED→GREEN evidence
- [x] Hand over exact source verification and explicit not-deployed/not-live-tested limits, retain proof logs and user-owned Git state
## Execution ledger
Native execution reuses ignored `data/telegram-presentation-20261107/progress.md` instead of helper scripts that manage Git state. No staging, commit or worktree action is part of this plan.  
Final frozen-source gate: 648 passed, one credential-gated live skip and three known upstream warnings on Python 3.14 in 211.29 seconds, final presentation/SSE/managed-key-pool slice 71 passed, repository-wide Ruff format/lint, ty, offline lock and deterministic OpenAPI snapshot checks passed. The review's post-output fallback and split-code findings were reproduced before their fixes, logical sentence/paragraph/table-row/list/reference/code and escaped-metadata regressions pass. Proof logs remain in the ignored execution directory, the Git index and live stack are unchanged. Real Telegram/client rendering, animation, Stop, menu caches, third-party SSE endpoints, a fresh production 3.12/container build and hosted CI are not certified by this source-only pass.  
