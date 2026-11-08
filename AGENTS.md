# AGENTS.md

## Working rules

- These instructions apply to the whole repository
- Check the worktree before editing and preserve unrelated user changes, use read-only Git commands and leave staging, commits, pushes, tags and releases to the user
- Use PowerShell 7 and the project Python through `uv`, do not create worktrees unless requested
- Keep Markdown thoughts on one line, use two trailing spaces for hard line breaks, do not manually wrap prose or churn spacer lines
- `README.md` and `docs/*.md` explain current behavior, update the relevant guide when behavior changes and keep release checks in `docs/release.md`, avoid dated implementation diaries, duplicated plans and historical test counts
- Keep agent guidance limited to development constraints, user setup and command instructions belong in the human-facing docs

## Application boundaries

- Build production wiring through `build_context(Settings)` in `src/pzp_ai_bot/app_context.py`, it owns logging, schema upgrades, providers, sessions, memory initialization, runtime settings and admins
- Use `build_context(settings, ensure_schema=False)` only when a test or tool owns schema setup, prefer the offline `context` fixture in `tests/conftest.py`
- Services use `session_scope(...)` and repositories, let the session boundary commit or roll back
- Schema changes need matching SQLAlchemy models, an Alembic revision and focused upgrade/runtime checks
- Preserve public facades in `db/repositories.py`, `memory/service.py` and `telegram/router.py`, put implementations in their existing repository, service and router modules
- Use `Scope`, `parse_scope`, `Scope.contains` and repository `scope_filter` for scope semantics, preserve persisted/exported `Scope.key` and `Scope.filename_stem`
- Record learning, settings, deletion, review, note, profile, retention and other admin mutations through the existing audit-event patterns
- SQLite is authoritative, Markdown is a post-commit projection, failed AI summaries must leave source messages retryable without creating durable raw-chat fallback content

## Telegram and authorization

- Keep router handlers, `COMMAND_DESCRIPTIONS`, help text, tests and `docs/commands.md` synchronized when commands change, startup clears the default command menu
- Use `safe_answer` and `safe_answer_document`, mark intended Markdown explicitly and keep raw status/config values literal, preserve streaming ownership and bounded reply/export payloads
- Reuse router admin guards for chat operations and `context.admins.is_bot_admin(...)` for global operations, authorize the actual target scope
- Preserve TTL-bound, one-time confirmations for `/forget` and confirmed `/settings`, retain exact-action step-up and optimistic versions for sensitive control-plane mutations
- Control-plane delegated-grant management belongs to bot admins, a scoped admin cannot delegate onward or manage grants

## Configuration and providers

- `Settings` owns environment and `.env` configuration, `show-config`, logs, audits and API errors must exclude secret values
- Keep secrets in deployment variables or the credential vault, prefer `api_key_env` references in profile examples, custom references read the process environment rather than arbitrary `.env` keys
- Provider selection caches include profile/model/revision identity, revisit invalidation when adding provider state
- Keep redaction at the OpenAI-compatible request boundary and preserve explicit local path/origin allowlists, preparation and download limits
- Embedding identity includes semantic configuration, dimensions alone do not establish compatibility, use explicit migration and activation on populated memory
- Extend `SAFE_RUNTIME_SETTING_TYPES` and the shared parsers/formatters for settings or command changes, synchronize defaults, validation, Telegram, CLI, UI, tests and docs
- Profile/template names stay lowercase `a-z`, digits, `_` and `-`, at most 64 characters

## Verification

- Use `ty` for type checking, do not introduce `mypy`
- Start with relevant tests, keep default checks offline and real Telegram tests behind `telegram_live`
- Tests should distinguish a plausible bug with independent expected values, retain useful smoke/security/packaging contracts and avoid exact internal inventories or source-text assertions when behavior is already covered
- Run the checks appropriate to the change and report their actual scope, documentation-only edits need command/link/diff validation rather than the whole application suite
- Two-space Markdown hard breaks may trigger `git diff --check`, preserve intentional breaks
