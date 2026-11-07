# Deployment

PZP AI can run directly in polling mode for local or bot-only operation, or as a three-service Docker Compose deployment where Caddy terminates public HTTPS, Nuxt renders the control plane and the bot runs its FastAPI webhook application. The production containers are separate so frontend, backend and ingress changes can be built and replaced independently.

## Polling

Polling is the simplest runtime and works well for local development, private deployments and first production trials.

```powershell
uv run pzp-bot polling
```

Polling clears any configured Telegram webhook first because Telegram update modes are mutually exclusive.

## Webhook

Set the webhook runtime variables:

```env
RUNTIME_MODE=webhook
WEBHOOK_URL=https://example.com/telegram/webhook
WEBHOOK_SECRET=<random-secret>
```

Then run:

```powershell
uv run pzp-bot webhook
```

The FastAPI endpoint rejects requests whose `X-Telegram-Bot-Api-Secret-Token` header does not match `WEBHOOK_SECRET`. Webhook startup also rejects blank secrets and common placeholders such as `change-me`, `changeme`, `placeholder`, `default` and `secret`; generate a deployment-specific random value before enabling webhook mode.

Webhook deployments should probe `GET /health`. It returns HTTP 200 with `pzp-idle-summarizer` and `pzp-runtime-settings-refresher` set to `true` only while both critical background task handles are running; it returns HTTP 503 with the failed task set to `false` when either loop has stopped. Ordinary iteration failures are retried with bounded exponential backoff and do not make health fail unless the task itself terminates.

When `CONTROL_PLANE_ENABLED=true`, the webhook FastAPI application also installs Telegram OIDC, Mini App exchange, session, provider, bot-settings and managed-chat endpoints plus the `chat_shared` enrollment handler. The public origin must be the exact HTTPS origin used by the browser and registered with BotFather. Caddy routes `/api/control` and `/auth` (including descendants), plus exact `/telegram/webhook` and `/health`, directly to FastAPI, and sends every other route to Nuxt SSR. Reserved backend paths never fall through to frontend HTML.

## Combined Loopback Development

From `web/`, `pnpm dev` coordinates a dedicated loopback FastAPI development runtime and the Nuxt development server. This is not the production webhook topology: mock authentication exists only in the dedicated `pzp-bot control-plane-dev` command, HTTP is accepted only for an exact loopback browser origin, and the normal webhook application retains its exact HTTPS/OIDC/master-key bootstrap checks.

With `CONTROL_PLANE_DEV_USER_ID` set and `WEBHOOK_URL` empty, the combined runtime performs no Telegram API operation even when `TELEGRAM_BOT_TOKEN` is present. It does not create a Telegram bot object, register a webhook route or start admin-alert delivery, while the database, critical background tasks, `/health`, bot settings, existing managed-chat state and scoped settings remain available. Group enrollment, delegated-admin target verification and leave therefore return a controlled unavailable response until connected mode is enabled. Configure a complete HTTPS `WEBHOOK_URL`, non-placeholder `WEBHOOK_SECRET` and bot token only when real connected development is intentional; startup then registers commands, calls `setWebhook`, receives webhook and `chat_shared` enrollment updates, enables membership checks/leave and starts admin-alert DMs. This changes Telegram's delivery mode and requires a publicly reachable HTTPS webhook.

`pnpm dev:web` runs Nuxt alone, while `pnpm build` followed by `pnpm start` runs only the production Nuxt output. Neither production command starts Python or enables mock authentication. The production deployment remains the Caddy/Nuxt/FastAPI Compose topology below.

## Docker

The bot image uses a multi-stage `python:3.12-slim` build, installs the latest uv through Astral's standalone installer, installs the exact production dependency set from `uv.lock` through frozen syncs, installs the project non-editably so the copied runtime environment is self-contained, runs as the non-root `pzp` user and includes a `pzp-bot doctor` image healthcheck. The dependency layer copies `pyproject.toml`, `uv.lock` and `README.md` before application source so ordinary source edits reuse the locked dependency layer. The installer version intentionally floats, application dependency resolution does not.

The frontend image uses dependency, build and runtime stages based on `node:26-bookworm-slim`, installs pnpm 11.28.11 through its standalone installer (without relying on the removed bundled Corepack), installs the frozen production dependency graph with a persistent BuildKit pnpm store and bounded install/build memory, runs `pnpm build`, copies only Nuxt's `.output` directory and runs `.output/server/index.mjs` as the non-root `node` user. Caddy uses the deliberately mutable major tag `caddy:2-alpine`, only that service publishes host ports 80 and 443, while Nuxt port 3000 and FastAPI port 8080 remain internal. Record the actual uv, Node, pnpm and Caddy versions/image digests when qualifying a deployment, a floating tool/base-image policy is not a promise of byte-identical future builds.

```powershell
docker build --tag pzp-ai:local .
docker run --rm -e AI_PROVIDER=fake -e DB_URL=sqlite+aiosqlite:////tmp/pzp/bot.db -e MEMORY_DIR=/tmp/pzp/memory pzp-ai:local pzp-bot doctor
docker run --rm -e AI_PROVIDER=fake -e DB_URL=sqlite+aiosqlite:////tmp/pzp/bot.db -e MEMORY_DIR=/tmp/pzp/memory pzp-ai:local pzp-bot migrate
```

Compose keeps `.env` optional so configuration inspection and one-off offline image commands can run before local secrets exist:

```powershell
docker compose config
docker compose run --rm bot pzp-bot doctor
```

The main Compose topology is a production webhook stack, not a polling stack. Before starting it, create `.env` and configure at least `TELEGRAM_BOT_TOKEN`, `BOT_ADMINS`, `WEBHOOK_URL=<CONTROL_PLANE_PUBLIC_ORIGIN>/telegram/webhook`, a non-default `WEBHOOK_SECRET`, `CONTROL_PLANE_ENABLED=true`, the exact HTTPS `CONTROL_PLANE_PUBLIC_ORIGIN`, Telegram OIDC credentials, the control-plane master-key map and provider settings. Optional `PZP_HTTP_PORT` and `PZP_HTTPS_PORT` values change the published host ports without changing the internal contract. `CADDY_SITE_ADDRESS` defaults to the public control-plane origin; set it separately to `:80` only when a trusted external ingress such as cloudflared terminates public TLS and forwards to the otherwise private/local Caddy listener, while `CONTROL_PLANE_PUBLIC_ORIGIN` remains the exact external HTTPS origin used for browser, Mini App and OIDC validation.

Build and start all three services with:

```powershell
docker compose up --build
```

The compose file mounts `./data` at `/app/data` so SQLite and Markdown memory survive restarts, and named `caddy_data` and `caddy_config` volumes preserve ingress state. The bot healthcheck probes its own `/health`, the frontend image probes `/login`, and Caddy validates its mounted configuration.

Build or replace one application image without rebuilding the other:

```powershell
docker compose build frontend
docker compose up -d --no-deps frontend
docker compose build bot
docker compose up -d --no-deps bot
```

`uv run pzp-bot polling` remains the supported local and manual polling command. Polling does not expose FastAPI or the browser control plane, so a control-plane deployment uses the Compose webhook topology.
The manually dispatched GitHub Actions workflow installs the latest uv, runs `uv lock --check`, then uses `uv sync --frozen --extra dev,local`. The pnpm setup action reads `web/package.json` through its explicit `working-directory: web` input. CI rejects project/lock drift, frozen installs do not rewrite either lockfile, and dependency upgrades must deliberately regenerate and review their lock changes. Keep tool versions floating as requested while recording their actual versions in acceptance evidence.

## Runtime Checklist

The [2026-11-07 live acceptance ledger](live-acceptance-2026-11-07.md) records the exact current test images, persistent Quick Tunnel, isolated mounts, runtime/privacy/recovery results, owner-confirmed authentication/session checks and remaining operator gates. The test ingress is loopback-only behind cloudflared, its ephemeral hostname is not a production DNS/availability commitment. The pnpm Docker cache is kept separate from the standalone installation tree so BuildKit cannot hide the installed CLI, and the installer version matches `web/package.json`'s `packageManager`.  

- Run `pzp-bot migrate` before starting a new database.
- Run `pzp-bot doctor` after changing secrets, provider profiles, database paths or memory paths.
- For webhook mode, configure the orchestrator's runtime readiness/liveness probe against `GET /health` and investigate any HTTP 503 before routing Telegram traffic.
- For polling mode, monitor process state plus `background_task_iteration_failed` and `background_task_stopped_unexpectedly` structured events because polling does not expose an HTTP health route.
- Verify Caddy routes `/api/control`, `/auth`, `/telegram/webhook` and `/health` to FastAPI and strips `Cookie`, `Authorization` and `X-CSRF-Token` before frontend requests; the Nuxt service must never receive backend credentials.
- Run `pnpm build`, `pnpm scan:generated` and `pnpm smoke:ssr` before replacing the frontend image.
- Keep `.env`, SQLite files and generated memory out of git.
- Use [operations.md](operations.md) for audit, backup, integrity and maintenance commands.
