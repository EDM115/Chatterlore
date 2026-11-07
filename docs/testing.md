# Testing

The default test suite is offline and deterministic. It does not connect to Telegram or external AI providers.

```powershell
uv lock --check
uv sync --frozen --extra dev
uv run pytest
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
pnpm openapi:check
pnpm build
pnpm scan:generated
pnpm smoke:ssr
pnpm test:e2e
```

After a Playwright upgrade, install its matching browser with `pnpm playwright install chromium`. Do not run Nuxt unit tests and production builds concurrently because they share generated configuration and type directories.  
Run `pnpm typecheck` directly rather than through RTK because RTK's `typecheck` shortcut invokes `tsc` instead of the Nuxt typecheck script. Use the declared `pnpm test:e2e` script for Playwright; it rebuilds the production SSR output and runs against `.output/server/index.mjs` in desktop Chromium and Pixel 7 profiles. The asset scanner checks the complete `.output` tree for server-only configuration names, development-auth markers, credential patterns, database URLs, private-key material and sensitive values loaded from local environment files. The SSR smoke launches the production Node entrypoint separately and verifies public/protected direct HTML, response security headers, distinct per-response CSP nonces on every inline script without `script-src 'unsafe-inline'`, the neutral authentication gate and absence of deployment-secret sentinels from returned documents and hydration data.

## Behavioral Regression Policy

Coverage instrumentation, pytest-cov, its transitive coverage package and the percentage floor were deliberately removed on 2026-11-07. Plain pytest remains required, with focused regressions for authorization, privacy, destructive operations, migrations, concurrency and provider boundaries. Plan 016 and dated measurement results remain historical evidence, not instructions to restore a coverage gate.

Green offline tests do not close real Telegram/OIDC/client acceptance, image and proxy smoke, two-key live failover/admin-DM delivery, deployed restore/restart or elapsed-age retention. The credential-gated live test currently checks `get_me()` only, the broader release matrix must be recorded separately. Keep the declared fixture event-loop scope explicit and do not suppress upstream warnings to make a run look clean.

## Behavior Map

- Independent routing and provider compatibility live in `tests/test_operation_routing.py` and `tests/test_provider_operation_profiles.py`: profile/model precedence and resets, all content/query/summary routes, embedding epoch retries, SDK-level OpenAI/DeepSeek/generic payloads and effective reasoning, endpoint-specific credential isolation, local origin/path limits, disabled capabilities and profile dependency conflicts
- Embedded inference lives in `tests/test_local_inference.py`: real tiny locally constructed tokenizer/causal model and Sentence Transformer fixtures (no downloads), generated-token-only decoding, offline safe loading, chat-template/context limits, duplicate loads, concurrency/cancellation/timeouts and malformed vector rejection. These real optional-package cases skip if the local extra is absent, run `uv sync --extra dev --extra local` and this file explicitly to establish actual local execution
- Index storage and owner controls live in `tests/test_embedding_indexes.py` and `tests/test_embedding_index_controls.py`: legacy upgrades and bounded fingerprint backfill, exact-space/freshness retrieval, SQL-only coverage and bounded payload reads, source edits/deletes, leased resumable jobs, concurrent activation/write fencing, bot-owner-only API/CLI/Telegram workflows, exact confirmation/fresh verification, metadata zero-inference behavior and bounded preparation. `web/test/mixed-provider-ui.nuxt.spec.ts` and `web/test/embedding-indexes.nuxt.spec.ts` cover route inheritance, local forms and explicit migration actions
- Provider and AI behavior lives in `tests/test_ai_providers.py`: OpenAI-compatible retry/redaction payloads, local optional-dependency failure messaging, provider-profile fail-closed behavior, CLI doctor diagnostics, embedding usage metadata, insufficient-context answers and prompt/model/reasoning usage metadata.
- Telegram integration lives in `tests/test_telegram_integration.py`: aiogram dispatcher-fed command and normal-message updates, mocked Bot API methods, valid webhook dispatch, polling startup sequencing, command matrix coverage, ingestion edge cases, `/settings` view/edit/preview/confirm/reject paths, Telegram API failure/rate-limit handling and export chunking.
- Operations coverage lives in `tests/test_operations.py`, `tests/test_cli.py`, `tests/test_admin_service.py` and `tests/test_deployment_artifacts.py`: persisted forget confirmations across context restart, expired confirmation rejection, runtime settings refresh/audit events, audit scope filtering, configurable admin-cache TTL/stats, SQLite maintenance helpers/CLI commands, successful CLI import/export/summarize/show-config paths, Dockerfile structure and GitHub Actions gate coverage.
- Product controls live in `tests/test_product_controls.py`, `tests/test_telegram_integration.py` and `tests/test_cli.py`: scoped memory status and topic discovery, skipped-message diagnostics, Telegram Desktop JSON import, Bot API update dump import, representative import/export/forget memory quality, memory item browse/delete/refresh, `/settings` preview/confirmation, document metadata ingestion and compact source references.
- `tests/test_telegram_html.py` and `tests/test_telegram_import_formats.py` cover the shared JSON/HTML import pipeline, actual Telegram-shaped text/entities/links/attachments, joined and forwarded authors, UTC timestamps, bounded local pagination, unsafe/cyclic paths, malformed input, aggregate-only forum imports, explicit target-thread conflicts, content-free checkpoints and cross-format retry idempotency with provider calls outside write transactions. `tests/test_scope.py` and the dispatcher-fed forum regression prove General uses topic 1 and cannot retrieve sibling or aggregate memory, while explicit chat scope remains hierarchical
- Control-plane coverage lives in `tests/test_control_plane_identity.py`, `tests/test_control_plane_mini_app.py`, `tests/test_control_plane_oidc.py`, `tests/test_control_plane_sessions.py`, `tests/test_control_plane_authorization.py`, `tests/test_control_plane_api.py`, `tests/test_control_plane_management.py`, `tests/test_control_plane_development.py`, `tests/test_secret_vault.py`, `tests/test_provider_credentials.py`, `tests/test_provider_secret_service.py`, `tests/test_provider_key_pool.py` and `tests/test_admin_alerts.py`: fail-closed production settings, loopback-only command-scoped development settings, stable local key behavior, offline zero-Telegram-call runtime, optional connected lifecycle, development login/step-up isolation, revisions 0013–0017, strict Mini App signatures, fixed-RS256 OIDC/PKCE/nonce validation, encrypted one-time transactions, hash-only sessions/CSRF, action-bound step-up, bootstrap/managed/delegated/native authorization across browser and Mini App sessions, foreign/topic/revoked/detached boundaries, requester-bound `requestChat` enrollment, managed-chat lifecycle/ingestion/summary gates, safe runtime/scoped settings, live grant invalidation, envelope encryption, managed/environment overlay precedence, optimistic conflicts, controlled reveal/rewrap, SDK failure classification, sticky bounded selection, same-key retry, restart/cross-instance concurrency, chat/embedding cache invalidation, safe telemetry, cooldown recovery, pool exhaustion, deduplicated alert intent, current-admin delivery and Telegram retry/unreachable behavior. `web/test/dev-coordinator.unit.spec.ts` covers the combined runner's exact child specifications, prefixed streams and sibling shutdown; `web/test/session.nuxt.spec.ts` covers browser restoration, one-time Mini App exchange, development auto-login and the OIDC login route; `web/test/sensitive-reveal.nuxt.spec.ts` proves exact-action step-up, default masking, explicit copy and 30-second/visibility/identity clearing; `web/test/providers.nuxt.spec.ts` covers post-hydration bot-admin provider health, explicit paid checks and sanitized alert retry; `web/test/management.nuxt.spec.ts` covers bot settings, prepared Mini App enrollment and browser DM fallback.
- Phase 7 business-API coverage lives in `tests/test_control_plane_phase7.py`: safe capability catalogs, backward-compatible and split retrieval/chat selection, authorized Search/Ask metadata and insufficient-context behavior, owning-scope memory/review mutations, explicit audited raw sources, cancellable streamed exports, SQL-aggregated stats, safe audit/usage pages and explicit diagnostics. Its query-count case seeds 150 items/events and fixes service SQL counts at one memory query, one audit query, seven overview queries and three timeseries queries while asserting page/time bounds and zero export initialization.
- Phase 8 coverage lives in `tests/test_control_plane_phase8.py` plus `web/test/phase8-*.nuxt.spec.ts`: atomic versioned credential replacement, secret-safe diagnostics, Dashboard metrics, complete settings/provider/chat workflows, split-profile Query and cancellation, Memory raw-source/action/export behavior, Stats/Audit/Operations presentation, shared 401/403/409/422/429 feedback, session expiry, Telegram chrome/dirty forms and accessibility foundations. `web/e2e/phase8.spec.ts` runs direct production-SSR hydration and Query/Memory/Telegram interaction paths at desktop and Pixel 7 sizes without real provider or Telegram calls.
- Optional live Telegram smoke coverage lives in `tests/test_telegram_live.py`. It is marked `telegram_live`, skipped unless `TELEGRAM_LIVE_BOT_TOKEN` and `TELEGRAM_LIVE_CHAT_ID` are set, and should not run in default CI.

Run live Telegram smoke explicitly only against a disposable chat:

```powershell
uv run pytest -m telegram_live tests/test_telegram_live.py
```

For a manual combined offline smoke, set a disposable `DB_URL`, `MEMORY_DIR`, positive `CONTROL_PLANE_DEV_USER_ID`, exact loopback `CONTROL_PLANE_DEV_ORIGIN`, matching `CONTROL_PLANE_DEV_API_PORT`/`PZP_API_DEV_ORIGIN`, and an empty `WEBHOOK_URL`; then run `pnpm dev` from `web/`. Wait for both prefixed servers, open `/providers`, `/bot-settings` and `/chats`, verify the anonymous session is replaced automatically by the configured development administrator, exercise safe runtime/scoped settings against disposable state, and confirm `GET /health` returns HTTP 200 with `pzp-idle-summarizer` and `pzp-runtime-settings-refresher` both true. Group enrollment, delegated-admin membership verification and leave should fail closed in this Telegram-offline mode. Stop with Ctrl+C and verify both child processes exit and both ports are released. Connected enrollment acceptance is separate and deliberately requires a public HTTPS webhook plus disposable Telegram configuration.

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
docker build --tag pzp-ai:local .
docker build --tag pzp-ai-web:local ./web
docker run --rm -e AI_PROVIDER=fake -e DB_URL=sqlite+aiosqlite:////tmp/pzp/bot.db -e MEMORY_DIR=/tmp/pzp/memory pzp-ai:local pzp-bot doctor
docker run --rm -e AI_PROVIDER=fake -e DB_URL=sqlite+aiosqlite:////tmp/pzp/bot.db -e MEMORY_DIR=/tmp/pzp/memory pzp-ai:local pzp-bot migrate
docker compose run --rm bot pzp-bot doctor
docker compose down
```

These commands do not start polling or the Compose webhook stack and do not connect to Telegram. A full deployment acceptance additionally requires a configured disposable webhook/OIDC environment, `docker compose up -d --build --wait`, direct checks of `/`, `/login`, `/health`, reserved-path failure behavior and headers, Telegram webhook delivery, `docker compose up -d --no-deps frontend` replacement and restart recovery.

The optional Playwright suite runs `pnpm test:e2e` against the declared production Nuxt Node server in desktop Chromium and Pixel 7 profiles. Authenticated client tests use deterministic same-origin API interception for every privileged route, Query/Memory source interactions and injected Telegram chrome/closing-confirmation behavior, while JavaScript-disabled tests prove protected direct routes contain only the neutral access gate before hydration and `/login` carries the private SSR policy. The outer test timeout remains 90 seconds for slower Windows runners, while production page assertions complete independently of Nuxt development compilation.

## CI

The [2026-11-07 maintenance ledger](project-review-2026-11-07.md#final-maintenance-verification-2026-11-07) records its earlier offline snapshot and interpreter/frontend gates. The later [mixed-provider and Telegram import acceptance](mixed-provider-acceptance-2026-11-07.md#final-corrected-source-verification) records the current 602-pass Python source, upstream warnings, production 3.12 image and scoped real-client evidence. Counts are snapshot-specific, earlier Docker unavailability and revoked-key results do not describe the current live stack, the complete current-source Python 3.12 suite and manually dispatched hosted CI remain separate gates.

`.github/workflows/ci.yml` intentionally runs only through `workflow_dispatch`. It installs the latest uv without a version pin and runs `uv lock --check`, `uv sync --frozen --extra dev,local`, format check, lint, `ty`, plain pytest, Alembic upgrade plus metadata drift detection against the same isolated runner-temp database, CLI help, `doctor` and a Docker build/run smoke. A separate frontend job reads pnpm 11.28.11 and Node major 26 from `web/package.json` through `pnpm/setup@v3` with `working-directory: web`, uses a frozen install and runs format, lint, Nuxt typecheck, unit/component, OpenAPI drift, SSR build, generated-browser-asset secret scan and production SSR smoke gates. The Docker job builds both independent images, while the bot image receives the offline doctor run. Installer and base-image versions deliberately float, application dependencies remain frozen in their committed locks. The optional `telegram_live` test, frontend Playwright suite and live three-container proxy acceptance are intentionally excluded from default CI, prior hosted green results do not qualify a new pushed snapshot.
