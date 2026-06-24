# Release Checklist

## Versioning

- The pyproject minor version tracks the current implementation pass and patch versions track bugfix passes inside the pass
- `PLAN.md` has its own roadmap version and should be updated after each meaningful implementation pass

## Pre-Release Checks

- Confirm Alembic head and note the migration ID included in the release
- Run `uv sync --extra dev --extra local`, `uv run ruff format --check .`, `uv run ruff check .`, `uv run ty check`, `uv run pytest`, `uv run alembic upgrade head`, `uv run pzp-bot --help` and `uv run pzp-bot doctor`
- Run Docker smoke checks: `docker compose config`, `docker build --tag pzp-ai:0.9.0 .`, container `pzp-bot doctor`, container `pzp-bot migrate` and `docker compose run --rm bot pzp-bot doctor`
- Review config changes in `docs/setup.md`, `docs/deployment.md`, `docs/secrets.md` and `docs/operations.md`
- Run a manual Telegram smoke test for command registration, `/help`, `/learn_status`, `/runtime_settings`, `/audit_events`, `/ask`, `/memory_export`, `/settings` preview/confirm and `/forget` confirmation before inviting a real group
- Confirm the GitHub Actions workflow has passed after pushing the branch

## Release Notes

- Include migration state, new environment variables, runtime setting keys, admin command changes, privacy/audit changes, Docker smoke result, CI result and any manual Telegram smoke-test result

## 0.9.0 Release Notes

- Package version is `0.9.0`.
- Migration head remains the current Alembic head from the existing migration chain.
- `Dockerfile` is multi-stage and non-root, `.dockerignore` excludes local state, compose validates without a local `.env`, and Docker smoke has been verified locally.
- `.github/workflows/ci.yml` runs format, lint, type check, tests, migration, CLI and Docker smoke gates on GitHub Actions.
- Run `pzp-bot import-telegram-json --file result.json --chat-id <id> [--thread-id <id>]` only on reviewed Telegram Desktop exports or Bot API update dumps and verify the target with `pzp-bot memory-status --scope <scope>` after import.
- Manual Telegram smoke should include `/memory_status`, `/memory_scopes`, `/memory_debug`, `/memory_items`, `/memory_item_refresh`, `/memory_item_delete` on a disposable item, `/settings top_k=N` followed by `/settings confirm=<token>`, and `/forget` confirmation in a disposable scope.