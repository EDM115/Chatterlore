# Testing

Use the offline suite for application regressions, production SSR/browser checks for the frontend and disposable live environments for Telegram/provider/deployment acceptance. Keep those results separate in the [release checklist](release.md), a passing mocked request does not establish a real client's behavior.

## Python checks

Run from the repository root:

```powershell
uv sync --frozen --extra dev
uv run ruff format --check .
uv run ruff check .
uv run ty check
uv run pytest -m "not telegram_live"
```

Python 3.12 is the production-image baseline. Use the project's uv environment rather than an unrelated system interpreter, `ty` is the type checker. `uv lock --check` provides an optional local lock-consistency check, frozen synchronization is the CI installation contract. Plain pytest is the regression gate, the repository has no coverage instrumentation or percentage floor.  
The `context` fixture builds a normal application with a temporary SQLite database/memory directory and fake provider, tests isolate settings from repository `.env`. Prefer it over production-service hand-wiring. Per-test engine ownership disposes contexts created by synchronous API fixtures as well as async fixtures. Keep loop scope explicit and investigate thread/closed-loop warnings rather than suppressing them.  
`AI_PROVIDER=fake` avoids remote AI calls in manual command checks, Telegram connectivity still depends on the runtime you start. Default tests mock network/provider boundaries, local-package tests use constructed artifacts without downloads. To exercise actual optional embedded dependencies:

```powershell
uv sync --frozen --extra dev --extra local
uv run pytest tests/test_local_inference.py
```

Optional cases skip when local packages are absent, a skipped run does not establish embedded inference. For release checks run the complete suite with the local extra installed. Check relevant warnings, including upstream AnyIO deprecation and SQLite expression-index reflection limitations, without treating them as proof of application failure or hiding unrelated warnings.

## Choose tests by behavior

| Change | Relevant test files |
| --- | --- |
| Scope, learning, reviews, notes and summaries | `test_scope.py`, `test_memory_service.py`, `test_memory_tools.py`, `test_memory_quality.py`, `test_product_controls.py` |
| Telegram commands, delivery and runtime | `test_telegram_commands.py`, `test_telegram_integration.py`, `test_telegram_presentation.py`, `test_telegram_streaming.py`, `test_chat_streaming.py` |
| JSON/HTML transfer and provenance | `test_telegram_html.py`, `test_telegram_import_formats.py`, `test_cli.py` |
| Provider requests, routes and local inference | `test_ai_providers.py`, `test_operation_routing.py`, `test_provider_operation_profiles.py`, `test_local_inference.py` |
| Index coverage, migration, adoption and activation | `test_embedding_indexes.py`, `test_embedding_index_controls.py`, `test_legacy_adoption.py`, `test_legacy_adoption_controls.py` |
| Identity, authorization, grants and sessions | `test_control_plane_identity.py`, `test_control_plane_mini_app.py`, `test_control_plane_oidc.py`, `test_control_plane_sessions.py`, `test_control_plane_authorization.py`, `test_control_plane_management.py` |
| Vault, credentials, failover and alerts | `test_secret_vault.py`, `test_provider_credentials.py`, `test_provider_secret_service.py`, `test_provider_key_pool.py`, `test_admin_alerts.py` |
| API contracts, settings and diagnostics | `test_control_plane_api.py`, `test_control_plane_development.py`, `test_control_plane_phase7.py`, `test_control_plane_phase8.py` |
| Operations, schema, wiring and deployment | `test_operations.py`, `test_admin_service.py`, `test_deployment_artifacts.py`, architecture/migration tests under `tests/` |

Add assertions that distinguish the intended behavior from plausible failures. Scope tests need a sibling/aggregate negative control, revocation tests need a previously authorized actor, migrations need existing data and rollback/fail-closed checks, imports need retry and partial-failure cases. Provider tests should inspect serialized SDK requests and prove no credential crosses endpoints. Index activation must reject stale coverage/epochs, streaming tests must reject post-output replay and source-only false success. Keep provider calls outside write transactions and verify failed operations leave business state retryable.  
For delivery, test text and caption commands through HTTP/dispatcher boundaries, immediate webhook acknowledgement, Stop ownership, replay suppression, overload/shutdown and ambiguous-send duplicate prevention. Test rendered chunk boundaries with long code/tables/Unicode and assert exact literal content survives fallback. Use SQL-count/payload bounds for scalability regressions, timing on one workstation is not a portable pass threshold.

## Frontend and API contracts

From `web/`:

```powershell
pnpm install --frozen-lockfile
pnpm runtime:check
pnpm format:check
pnpm lint
pnpm typecheck
pnpm test
pnpm openapi:check
pnpm build
pnpm scan:generated
pnpm smoke:ssr
pnpm test:e2e
```

Use the package scripts directly, `pnpm typecheck` runs Nuxt's checker. Do not run Nuxt unit tests and production builds concurrently, they share generated configuration/type directories. After a Playwright upgrade install the matching Chromium with `pnpm exec playwright install chromium`.  
The backend snapshot in `web/openapi/` and declarations in `web/app/types/` must match the application. `pnpm openapi:check` checks declarations against that committed snapshot, Python contract tests check the snapshot against the backend. From the repository root, `uv run python scripts/export_control_plane_openapi.py --check web/openapi/control-plane.openapi.json` checks the snapshot, omit `--check` to regenerate it. From `web/`, `pnpm openapi:types` regenerates client declarations after an intentional API change.  
The asset scanner inspects the complete `.output` tree for secret patterns, server-only configuration/development-auth markers, database URLs and sensitive local environment values. The SSR smoke starts the production Node server and checks public/protected direct HTML, private/no-store headers, distinct CSP nonces on inline scripts and a neutral pre-authentication gate without secret-bearing hydration data.  
`pnpm test:e2e` builds production SSR and runs desktop Chromium plus Pixel 7 emulation against `.output/server/index.mjs`. Privileged API interception supplies deterministic identities and exercises Query/Memory actions, session feedback and Telegram chrome. JavaScript-disabled tests inspect protected direct routes before hydration. These tests do not replace real Telegram Web/mobile or OIDC acceptance.

## Migration drift

Use a disposable database, applying `upgrade head` to an operator database changes it:

```powershell
$env:DB_URL = "sqlite+aiosqlite:///C:/path/to/disposable/migration-check.db"
uv run alembic upgrade head
uv run alembic check
```

Expect `No new upgrade operations detected.` Check both a fresh schema and a disposable copy of the prior release's backup before rollout. Model changes normally need a migration plus tests for upgrade/runtime behavior. SQLite cannot reflect expression indexes, focused schema tests inspect those indexes directly. Restore the intended `DB_URL` or start a fresh shell before running application commands.

## Offline command and image smoke

```powershell
uv run pzp-bot --help
uv run pzp-bot doctor
docker version
docker compose config --quiet
docker build --tag pzp-ai:local .
docker build --tag pzp-ai-web:local ./web
docker run --rm -e AI_PROVIDER=fake -e DB_URL=sqlite+aiosqlite:////tmp/pzp/bot.db -e MEMORY_DIR=/tmp/pzp/memory pzp-ai:local pzp-bot doctor
docker run --rm -e AI_PROVIDER=fake -e DB_URL=sqlite+aiosqlite:////tmp/pzp/bot.db -e MEMORY_DIR=/tmp/pzp/memory pzp-ai:local pzp-bot migrate
```

These image commands neither start polling nor contact Telegram. Set the host command environment to fake/disposable paths when testing without a real `.env`, `doctor` inspects configured state. Plain `docker compose config` can print expanded secrets, use its quiet validation form for shared output.  
Use [setup](setup.md) to run combined offline development with disposable paths, an exact loopback origin, positive development UID and empty `WEBHOOK_URL`. Verify `/health`, automatic development session bootstrap, safe settings changes, stale-save 409 handling and Ctrl+C cleanup of both child processes. Enrollment/membership/leave should remain unavailable offline. Connected development changes Telegram delivery mode, coordinate it with other consumers.

## Live acceptance

The optional test requires `TELEGRAM_LIVE_BOT_TOKEN` and `TELEGRAM_LIVE_CHAT_ID`:

```powershell
uv run pytest -m telegram_live tests/test_telegram_live.py
```

It calls `get_me()` only, the chat ID gates execution but this test does not send or validate a chat message. Plain `uv run pytest` also collects it and runs it when those credentials exist, use `-m "not telegram_live"` to enforce an offline run. Real provider checks need explicit disposable credentials and quotas, keep secrets and synthetic content out of saved evidence.  
Qualify the three-service public stack separately: TLS/headers, backend-path routing, frontend header stripping, real webhook delivery, browser/Mini App identity, independent frontend replacement, restart and key-injected restore. Run actual learning/scope/review/forget and elapsed-retention checks on disposable data. [Release](release.md) carries the full manual matrix and unresolved credential/client/capacity gates.

## CI

`.github/workflows/ci.yml` runs through `workflow_dispatch`, push and pull-request events do not start it. The Python job installs frozen `dev,local` dependencies on Python 3.12, runs Ruff/ty/plain pytest, isolated Alembic upgrade/drift, CLI help and doctor. It has no separate `uv lock --check` step. The frontend job uses `web/package.json`'s pnpm/managed Node contract and runs frozen install, formatting/lint/types/unit tests, declaration drift, SSR build, generated-asset scan and SSR smoke. The Docker job builds both images and runs offline doctor in the backend image.  
Playwright, real Telegram/provider checks and public Compose acceptance remain separate. Keep live credentials out of CI so plain pytest stays offline. A hosted result qualifies its exact published snapshot, record it again after release-affecting changes.
