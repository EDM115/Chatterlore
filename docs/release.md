# Release Checklist

Use this checklist before tagging or publishing a new PZP AI release. It is designed to keep migrations, docs, tests, Docker artifacts and live Telegram validation in sync.

## Versioning

- The pyproject minor version tracks the current implementation pass and patch versions track bugfix passes inside the pass.
- `PLAN.md` has its own roadmap version and should be updated only when the roadmap state changes.
- Keep README badges, Docker examples and release notes aligned with the published artifact names.

## Pre-Release Checks

- Confirm Alembic head and note the migration ID included in the release.
- Run `uv sync --extra dev --extra local`, `uv run ruff format --check .`, `uv run ruff check .`, `uv run ty check`, `uv run pytest`, `uv run alembic upgrade head`, `uv run pzp-bot --help` and `uv run pzp-bot doctor`.
- From `web/`, run `pnpm format:check`, `pnpm lint`, `pnpm typecheck`, `pnpm test`, `pnpm openapi:check`, `pnpm build`, `pnpm scan:generated`, `pnpm smoke:ssr` and `pnpm test:e2e`.
- Run Docker smoke checks: `docker compose config`, `docker build --tag pzp-ai:0.11.0 .`, `docker build --tag pzp-ai-web:0.11.0 ./web`, container `pzp-bot doctor`, container `pzp-bot migrate` and `docker compose run --rm bot pzp-bot doctor`.
- With disposable webhook/OIDC credentials, run the three-service Compose stack and verify Caddy TLS, reserved-path routing, credential-header stripping, service health, Telegram webhook delivery, independent frontend replacement and restart recovery.
- Review config changes in `docs/setup.md`, `docs/deployment.md`, `docs/secrets.md` and `docs/operations.md`.
- Run a manual Telegram smoke test for command registration, `/help`, `/learn_status`, `/runtime_settings`, `/audit_events`, `/ask`, `/memory_export`, `/settings` preview/confirm and `/forget` confirmation before inviting a real group.
- Confirm the GitHub Actions workflow has passed after pushing the branch.

## Release Notes Template

Include:
- Package version and migration state.
- New environment variables or runtime setting keys.
- Admin command changes and privacy/audit changes.
- Docker smoke result and GitHub Actions result.
- Manual Telegram smoke-test result.
- Any known limitations or operator action required before upgrade.

## Current Release Notes

- Package version is `0.11.0`.
- Migration head is `0014_provider_credential_pools`.
- The Python and Nuxt Dockerfiles are multi-stage and non-root, Caddy is pinned to 2.11.4 Alpine, Compose publishes only the ingress and the frontend/backend build independently. The prior bot-image Docker smoke is verified, but the new Nuxt/Caddy three-service build, TLS, routing, health and replacement acceptance remains pending until Docker Desktop is available.
- `.github/workflows/ci.yml` runs format, lint, type check, tests, migration, CLI and bot Docker smoke gates, builds the independent frontend image and runs the Nuxt SSR build/secret-scan/production-smoke gates on GitHub Actions; hosted results must be confirmed again after these workflow changes are pushed.
- The 2026-08-01 non-Docker SSR transition checkpoint passed Oxfmt, type-aware Oxlint/tsgolint, raw Nuxt typecheck, 11 unit/component tests, OpenAPI drift, a 2.75 MB production server build, a 17-text-asset secret scan, nonce-aware SSR smoke and 10 desktop/mobile Playwright checks. Whole-project Ruff format/lint, `ty` and the fresh offline suite also passed with 272 tests, 1 skipped live Telegram test and 4 strict expected failures.
- Run `pzp-bot import-telegram-json --file result.json --chat-id <id> [--thread-id <id>]` only on reviewed Telegram Desktop exports or Bot API update dumps and verify the target with `pzp-bot memory-status --scope <scope>` after import.
- Manual Telegram smoke should include `/memory_status`, `/memory_scopes`, `/memory_debug`, `/memory_items`, `/memory_item_refresh`, `/memory_item_delete` on a disposable item, `/settings top_k=N` followed by `/settings confirm=<token>`, and `/forget` confirmation in a disposable scope.
