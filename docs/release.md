# Release Checklist

## Versioning

- The pyproject minor version tracks the current implementation phase and patch versions track bugfix passes inside the phase
- `PLAN.md` has its own roadmap version and should be updated after each meaningful implementation pass

## Pre-Release Checks

- Confirm Alembic head and note the migration ID included in the release
- Run `uv run ruff format --check .`, `uv run ruff check .`, `uv run ty check`, `uv run pytest`, `uv run alembic upgrade head`, `uv run pzp-bot --help` and `uv run pzp-bot doctor`
- Review config changes in `docs/setup.md`, `docs/deployment.md`, `docs/secrets.md` and `docs/operations.md`
- Run a manual Telegram smoke test for command registration, `/help`, `/learn_status`, `/runtime_settings`, `/audit_events`, `/ask`, `/memory_export` and `/forget` confirmation
- Docker runtime verification and CI workflow gates are deferred until Docker is fully installed on this workstation

## Release Notes

- Include migration state, new environment variables, runtime setting keys, admin command changes, privacy/audit changes and any manual Telegram smoke-test result

## Phase 6 Release Notes

- Migration head is `0006_phase6_product_completeness`; it adds persisted scoped settings confirmations.
- Run `pzp-bot import-telegram-json --file result.json --chat-id <id> [--thread-id <id>]` only on reviewed Telegram Desktop exports and verify the target with `pzp-bot memory-status --scope <scope>` after import.
- Manual Telegram smoke should include `/memory_status`, `/memory_scopes`, `/memory_debug`, `/memory_items`, `/memory_item_refresh`, `/memory_item_delete` on a disposable item, and `/settings top_k=N` followed by `/settings confirm=<token>`.
- Docker runtime verification and CI workflow validation remain deferred until Docker is available on this workstation.
