# Testing

The default test suite is offline and deterministic. It does not connect to Telegram or external AI providers.

```powershell
uv lock --check
uv sync --frozen --extra dev
uv run pytest --cov=pzp_ai_bot --cov-branch --cov-report=term-missing
uv run ruff format --check .
uv run ruff check .
uv run ty check
```

Use `AI_PROVIDER=fake` for deterministic no-network behavior.

The Nuxt workspace has an independent frozen frontend gate:

```powershell
Set-Location web
pnpm install --frozen-lockfile
pnpm runtime:check
pnpm format:check
pnpm lint
pnpm typecheck
pnpm test
pnpm playwright test
pnpm openapi:check
pnpm generate
pnpm scan:generated
```

Run `pnpm typecheck` directly rather than through RTK because RTK's `typecheck` shortcut invokes `tsc` instead of the Nuxt typecheck script. `pnpm playwright test` uses the project Playwright binary and the pnpm-managed Node runtime. The asset scanner checks the generated client for server-only configuration names, credential patterns, database URLs, private-key material and sensitive values loaded from local environment files.

## Coverage Gate

The CI test command measures statement and branch coverage, prints missing lines and enforces the `pyproject.toml` floor of 78%. The starting live baseline measured on 2026-07-29 is 81%; the lower floor is a conservative regression alarm rather than a coverage target. Do not add superficial tests or omit production modules to chase the percentage: authorization, privacy, destructive operations, migrations, concurrency and other behavior-critical paths remain more important than the global number.

The passing floor does not close the skipped live Telegram smoke, the refreshed Docker runtime smoke, optional local-provider success behavior or uncovered configuration and Telegram error branches. Review the missing-line report when changing those surfaces.

## Coverage Map

- Provider and AI behavior lives in `tests/test_ai_providers.py`: OpenAI-compatible retry/redaction payloads, local optional-dependency failure messaging, provider-profile fail-closed behavior, CLI doctor diagnostics, embedding usage metadata, insufficient-context answers and prompt/model/reasoning usage metadata.
- Telegram integration lives in `tests/test_telegram_integration.py`: aiogram dispatcher-fed command and normal-message updates, mocked Bot API methods, valid webhook dispatch, polling startup sequencing, command matrix coverage, ingestion edge cases, `/settings` view/edit/preview/confirm/reject paths, Telegram API failure/rate-limit handling and export chunking.
- Operations coverage lives in `tests/test_operations.py`, `tests/test_cli.py`, `tests/test_admin_service.py` and `tests/test_deployment_artifacts.py`: persisted forget confirmations across context restart, expired confirmation rejection, runtime settings refresh/audit events, audit scope filtering, configurable admin-cache TTL/stats, SQLite maintenance helpers/CLI commands, successful CLI import/export/summarize/show-config paths, Dockerfile structure and GitHub Actions gate coverage.
- Product controls live in `tests/test_product_controls.py`, `tests/test_telegram_integration.py` and `tests/test_cli.py`: scoped memory status and topic discovery, skipped-message diagnostics, Telegram Desktop JSON import, Bot API update dump import, representative import/export/forget memory quality, memory item browse/delete/refresh, `/settings` preview/confirmation, document metadata ingestion and compact source references.
- Control-plane identity coverage lives in `tests/test_control_plane_identity.py`, `tests/test_control_plane_mini_app.py`, `tests/test_control_plane_oidc.py`, `tests/test_control_plane_sessions.py`, `tests/test_control_plane_authorization.py` and `tests/test_control_plane_api.py`: fail-closed settings, revision 0013, strict Mini App signatures, fixed-RS256 OIDC/PKCE/nonce validation, encrypted one-time transactions, hash-only sessions/CSRF, expiry/rotation/account switching, live grant invalidation, origin/CSRF/security headers, independent auth rate limits and content-free audit events. `web/test/session.nuxt.spec.ts` covers browser restoration, one-time Mini App exchange and the OIDC login route.
- Optional live Telegram smoke coverage lives in `tests/test_telegram_live.py`. It is marked `telegram_live`, skipped unless `TELEGRAM_LIVE_BOT_TOKEN` and `TELEGRAM_LIVE_CHAT_ID` are set, and should not run in default CI.

Run live Telegram smoke explicitly only against a disposable chat:

```powershell
uv run pytest -m telegram_live tests/test_telegram_live.py
```

## Migration Drift

Run the same migration gate as CI against a disposable database, never an operator database:

```powershell
$env:DB_URL = "sqlite+aiosqlite:///C:/path/to/disposable/pzp-ai-migration-check.db"
uv run alembic upgrade head
uv run alembic check
```

The drift check must exit successfully with `No new upgrade operations detected.` Any generated operation means the SQLAlchemy models and Alembic history disagree and requires either the missing schema revision or a narrowly justified comparison fix.

## Docker Smoke

```powershell
docker version
docker compose config
docker build --tag pzp-ai:0.9.0 .
docker run --rm -e AI_PROVIDER=fake -e DB_URL=sqlite+aiosqlite:////tmp/pzp/bot.db -e MEMORY_DIR=/tmp/pzp/memory pzp-ai:0.9.0 pzp-bot doctor
docker run --rm -e AI_PROVIDER=fake -e DB_URL=sqlite+aiosqlite:////tmp/pzp/bot.db -e MEMORY_DIR=/tmp/pzp/memory pzp-ai:0.9.0 pzp-bot migrate
docker compose run --rm bot pzp-bot doctor
docker compose down
```

These commands do not start polling and do not connect to Telegram.

## CI

`.github/workflows/ci.yml` pins uv 0.12.0 and runs GitHub Actions quality gates on pushes and pull requests to `main`: `uv lock --check`, `uv sync --frozen --extra dev`, format check, lint, `ty`, the branch-coverage pytest gate, Alembic upgrade plus metadata drift detection against the same isolated runner-temp database, CLI help, `doctor` and a Docker build/run smoke. A separate frontend job pins pnpm 11.18.0, lets pnpm resolve the locked Node 24.18.0 runtime, uses a frozen install, and runs format, lint, Nuxt typecheck, unit/component, OpenAPI drift, static generation and generated-asset secret-scan gates. Docker pins the same uv release and installs production dependencies from `uv.lock` through frozen syncs. The optional `telegram_live` test and frontend Playwright suite are intentionally excluded from default CI.
