# AGENTS.md

## Scope

- This file applies to the whole repository.
- `README.md` and `docs/*.md` are the canonical human-facing sources for setup, command reference, architecture, privacy, operations, deployment, secrets, testing and release checklists. Do not duplicate that material here; update those files when user-facing behavior changes.
- Keep Markdown prose as intentional one-line thoughts. Do not manually wrap lines to a fixed width, and use two trailing spaces for hard line breaks instead of empty spacer lines when editing Markdown in this repo.

## Roadmap Discipline

- Treat `PLAN.md` as the living lifecycle roadmap, not as an old planning scratchpad. After any meaningful implementation pass, update it so completed work, partial work, remaining backlog, test gaps, release gates, runbook notes, risks and changelog stay aligned with the actual code.
- Do not blur `DONE`, `PARTIAL` and `TODO` state in `PLAN.md`. If code lands without a matching plan update, future agents lose the main project state reference.
- For documentation-only roadmap work, it is enough to validate the intended file set and diff hygiene; do not spend time rerunning the whole application suite unless code changed in the same turn.
- `ty` is the type checker for this repo. Do not introduce `mypy` commands, config or expectations unless the user explicitly asks for a checker migration.

## Construction Boundaries

- Build application wiring through `build_context(Settings)` in `src/pzp_ai_bot/app_context.py`. It configures logging, runs Alembic schema upgrade by default, builds providers, creates the async session factory, initializes `MemoryService`, ensures the Markdown store exists, refreshes runtime settings and creates `AdminService`.
- Use `build_context(settings, ensure_schema=False)` only when a test or tool deliberately owns schema setup. Avoid hand-wiring `MemoryService`, providers or admin state in production paths.
- Tests should prefer the `context` fixture in `tests/conftest.py`; it uses a temp SQLite database, temp memory directory, fake provider and admin settings that keep tests offline and deterministic.
- Database access inside services should go through `session_scope(...)` and repository classes in `src/pzp_ai_bot/db/repositories.py`. Let `session_scope` commit or roll back; do not sprinkle manual commits through service methods.
- Schema changes usually require all three surfaces: SQLAlchemy models, an Alembic revision under `alembic/versions`, and focused tests proving upgrade/runtime behavior.

## Scope And Memory Rules

- Use `Scope` and `parse_scope` from `src/pzp_ai_bot/memory/scope.py` instead of parsing scope strings inline. Valid forms include `all`, `current`, `this_chat`, `this_topic`, `<chat_id>` and `<chat_id>:<thread_id>`; `main`, `none` or an empty thread component map to the main chat thread.
- Preserve the hierarchy semantics: global contains everything, a chat scope contains all its topics, and a topic scope only contains that topic. Use repository `scope_filter(...)` and `Scope.contains(...)` instead of ad hoc SQL conditions.
- Memory exports and filenames rely on `Scope.key` and `Scope.filename_stem`; keep those stable unless you are intentionally changing persisted/exported naming.
- When changing learning, settings, deletion, reviews, notes, profiles or retention behavior, add audit events through `EventRepository` or the existing `MemoryService` patterns. Admin-visible operations should stay explainable from `audit_events`.

## Telegram Command Coupling

- Telegram handlers are built in `src/pzp_ai_bot/telegram/router.py`, command registration and help text live in `src/pzp_ai_bot/telegram/commands.py`, and CLI commands live in `src/pzp_ai_bot/main.py`. When adding or renaming a Telegram command, keep the router handler, `COMMAND_DESCRIPTIONS`, `command_help_text()`, tests and `docs/commands.md` synchronized.
- Use `safe_answer` and `safe_answer_document` for Telegram replies. Existing handlers cap normal text responses around 3900 characters and split memory export documents below Telegram's 50 MB upload limit.
- Reuse the local admin checks: `require_admin(...)` for chat-admin-or-bot-admin commands, and `context.admins.is_bot_admin(...)` for global/all-scope operations that should be restricted to configured bot admins.
- Preserve confirmation flows for destructive or sensitive changes. `/forget` and confirmed `/settings` use token challenge repositories with TTLs rather than applying broad changes immediately.

## Settings And Providers

- Runtime configuration comes from `Settings` in `src/pzp_ai_bot/config.py`, loaded from environment variables and `.env` with extra keys ignored. `show-config` must remain non-secret; do not print tokens or provider keys there.
- `AI_PROVIDER_PROFILES` accepts either a JSON object keyed by profile name or a list of objects with `name`. Profiles may resolve secrets through `openai_api_key_env` / `api_key_env`; prefer that shape over embedding keys in JSON examples.
- Provider selection is cached by `(profile, chat_model, embedding_model)` in `ProviderRegistry`. If a change makes provider instances stateful in a new way, revisit that cache key.
- The OpenAI-compatible provider redacts prompt and embedding inputs before outbound calls when redaction is enabled. Keep redaction in the provider boundary for any new OpenAI-compatible request path.
- Runtime setting keys are constrained by `SAFE_RUNTIME_SETTING_TYPES`. Adding a setting normally means updating defaults, runtime validation, Telegram parsing, CLI parsing, formatting, tests and any docs that expose it.

## Parser And Formatting Patterns

- Command argument parsing helpers live close to the service layer in `src/pzp_ai_bot/memory/service.py` and are reused by Telegram and CLI code. Extend helpers such as `parse_runtime_settings_args`, `parse_scope_settings_args`, `parse_note_create_args` and `parse_note_edit_args` instead of implementing parallel parsers in handlers.
- User-visible text formatters are also centralized in `memory/service.py` as `format_*` helpers. When changing output, update the helper and the tests that assert the resulting text rather than formatting separately in each command.
- Names for profiles and templates are normalized to lowercase `a-z`, digits, `_` and `-`, with a 64-character limit. Do not loosen this without checking database uniqueness and command UX.

## Test Selection Hints

- Use targeted tests first when touching a narrow surface: `tests/test_memory_service.py` for memory service behavior, `tests/test_memory_tools.py` and `tests/test_memory_quality.py` for command/tool surfaces, `tests/test_scope.py` for scope grammar, `tests/test_telegram_commands.py` and `tests/test_telegram_integration.py` for Telegram handlers, `tests/test_ai_providers.py` for provider behavior, and `tests/test_operations.py` for runtime/maintenance commands.
- Keep default tests offline. Anything needing real Telegram credentials belongs under the existing `telegram_live` marker and must not become part of default CI.

## Source-Control Hygiene

- Before editing, check the worktree and preserve user changes. Ignore unrelated dirty files unless they affect the requested change.
- For Markdown-only changes that intentionally use two-space hard breaks, expect `git diff --check` may complain about trailing whitespace; do not churn those line endings away unless the user asks.
