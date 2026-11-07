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
- Run a manual Telegram smoke test for command registration, `/help`, `/learn_status`, `/runtime_settings`, `/audit_events`, `/ask`, `/memory_export`, `/settings` preview/confirm and `/forget` confirmation before inviting a real group. In the control plane, also validate browser and Mini App login, bot-owner add/remove and session revocation, prepared/DM group enrollment, delegated-admin boundaries, pause/resume/detach/leave/forget differences and chat/topic inherited settings against disposable accounts/chats.
- Manually dispatch the GitHub Actions workflow after pushing the final branch and record its result for that exact snapshot (push/PR events no longer trigger it automatically).
- The Plan 021 delegated-admin policy is resolved to bot-admin-only grant management, complete the remaining client/permission/enrollment/lifecycle, separate polling, disposable provider/DM and user-dispatched hosted CI checks in the [current live ledger](live-acceptance-2026-11-07.md) before release

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
- The [later 2026-11-07 execution](live-acceptance-2026-11-07.md) supersedes the earlier maintenance/runtime boundary below: 449 Python passes plus one live skip, 64 Vitest and 44 no-retry browser cases, current split images/TLS/header/privacy/recovery/elapsed-retention-on-copies and owner-confirmed real login/session persistence passed. Bot-admin-only delegated-grant management is enforced. The existing OpenAI environment key returns 401 and was confirmed revoked by the owner, who deferred the disposable-key/provider/DM gate until later. The test stack/tunnel remain running, this is not a production release or hosted CI pass
- The [2026-11-07 current offline checkpoint](project-review-2026-11-07.md#final-maintenance-verification-2026-11-07) passes 438 Python tests plus 1 live skip on both 3.12 and 3.14, 63 Vitest and 44 no-retry browser cases, current static/schema/migration/SSR/asset gates and isolated combined development. This closes maintenance qualification, not the remaining release/policy gates.
- Migration head is `0017_scope_settings_versions`.
- The Python and Nuxt Dockerfiles remain multi-stage and non-root, use standalone uv/pnpm installers, select latest uv and Node major 26 as requested, and keep application dependencies frozen. Caddy uses the floating major `2-alpine` tag, Compose publishes only the ingress and the frontend/backend build independently. Historical image builds and partial live progress do not qualify the November toolchain, fresh image/proxy/TLS/health/replacement/restart acceptance remains required and the local Docker daemon was unavailable during this review.
- `.github/workflows/ci.yml` runs format, lint, type check, plain tests (without coverage instrumentation), migration, CLI and bot Docker smoke gates, builds the independent frontend image and runs the Nuxt SSR build/secret-scan/production-smoke gates only when manually dispatched, hosted results must be confirmed again after these workflow changes are pushed.
- The historical 2026-08-09 Plan 021 checkpoint closed deterministic contracts, authentication/authorization, provider/vault/alerts, desktop/mobile browser and synthetic encrypted recovery gates through revision `0017`, with zero remaining xfails. Later notes record partial real Mini App/admin-revocation/delegated-access and image acceptance. These are dated observations, not a current full-client or deployed-runtime pass, live two-key failover/admin-DM, elapsed retention and final production recovery remain open.
- The 2026-11-07 maintenance pass reviews all numbered plans and all changed direct dependencies, removes coverage tooling, fixes target SQLAlchemy/typing contracts, stabilizes Python-dependent OpenAPI response descriptions and aligns Nuxt roots/imports/aliases. See the [project review](project-review-2026-11-07.md) and both linked dependency audits for source and verification boundaries, do not reuse the older checkpoint counts below as current evidence.
- The 2026-08-01 non-Docker SSR transition checkpoint passed Oxfmt, type-aware Oxlint/tsgolint, raw Nuxt typecheck, 11 unit/component tests, OpenAPI drift, a 2.75 MB production server build, a 17-text-asset secret scan, nonce-aware SSR smoke and 10 desktop/mobile Playwright checks. Whole-project Ruff format/lint, `ty` and the fresh offline suite also passed with 272 tests, 1 skipped live Telegram test and 4 strict expected failures.
- The 2026-08-01 Phase 5 provider-pool checkpoint passed immutable lock, Ruff format/lint, `ty`, a fresh complete offline suite with 303 passed/1 skipped/4 strict expected failures, empty-database upgrade/check through revision 0015, CLI help and fake-provider doctor. Nuxt passed format/lint/raw typecheck, 12 unit/component tests, OpenAPI drift, a 2.8 MB SSR build, 20-asset secret scan, direct providers-route SSR smoke and 14 desktop/mobile Playwright checks. Final-audit environment-credential immutability and active-pointer cleanup regressions passed 36 focused tests, then a second fresh full suite passed with the same 303/1/4 outcome and no warnings; ten non-fatal aiosqlite teardown warnings from the preceding checkpoint remain a recurrence watch item. Live multi-key/provider-DM, OIDC/Mini App and split Compose acceptance remain required before production deployment.
- The 2026-08-01 Phase 6 checkpoint adds reloadless managed bot owners and delegated chat admins, requester-bound prepared/DM group enrollment, active/paused/detached lifecycle enforcement across ingestion and idle summarization, separate leave/forget semantics, safe runtime and inherited scoped settings, synchronized OpenAPI contracts and SSR Bot Settings/Chats screens. Immutable lock, Ruff format/lint, `ty`, CLI help, fake-provider doctor, fresh upgrade/drift through revision 0016 and the warning-free full suite passed with 355 tests, 1 live skip and 4 strict expected failures; Nuxt passed format/lint/typecheck, 30 unit/component tests, OpenAPI drift, a 2.89 MB production SSR build, 236-asset secret scan, direct SSR smoke and all 22 desktop/mobile browser checks. Real prepared-button Telegram selection and split Compose/OIDC acceptance remain release-environment gates.
- Run `pzp-bot import-telegram-json --file result.json --chat-id <id> [--thread-id <id>]` only on reviewed Telegram Desktop exports or Bot API update dumps and verify the target with `pzp-bot memory-status --scope <scope>` after import.
- Manual Telegram smoke should include `/memory_status`, `/memory_scopes`, `/memory_debug`, `/memory_items`, `/memory_item_refresh`, `/memory_item_delete` on a disposable item, `/settings top_k=N` followed by `/settings confirm=<token>`, and `/forget` confirmation in a disposable scope.
