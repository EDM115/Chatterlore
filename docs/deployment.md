# Deployment

PZP AI can run in polling mode for simple deployments or webhook mode when you want Telegram to deliver updates to an HTTPS endpoint. Docker support is included for reproducible runtime checks and container deployment.

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

When `CONTROL_PLANE_ENABLED=true`, the webhook FastAPI application also installs the Phase 3 Telegram OIDC, Mini App exchange and session endpoints. The public origin must be the exact HTTPS origin used by the browser and registered with BotFather, and the reverse proxy must preserve secure cookies and route the fixed `/auth/telegram/callback` plus `/api/control/v1/auth/*` and `/api/control/v1/session` paths to this application. Phase 3 does not yet add generated Nuxt static assets to the Docker image or serve them from FastAPI, and polling mode still has no HTTP server; production control-plane deployment remains deferred until the later static-serving/runtime phase.

## Docker

The Docker image uses a multi-stage `python:3.12-slim` build, pins uv 0.12.0, installs the exact production dependency set from `uv.lock` through frozen syncs, installs the project non-editably so the copied runtime environment is self-contained, runs as the non-root `pzp` user and includes a `pzp-bot doctor` healthcheck. The dependency layer copies `pyproject.toml`, `uv.lock` and `README.md` before application source so ordinary source edits reuse the locked dependency layer.

```powershell
docker build --tag pzp-ai:local .
docker run --rm -e AI_PROVIDER=fake -e DB_URL=sqlite+aiosqlite:////tmp/pzp/bot.db -e MEMORY_DIR=/tmp/pzp/memory pzp-ai:local pzp-bot doctor
docker run --rm -e AI_PROVIDER=fake -e DB_URL=sqlite+aiosqlite:////tmp/pzp/bot.db -e MEMORY_DIR=/tmp/pzp/memory pzp-ai:local pzp-bot migrate
```

Compose keeps `.env` optional so smoke commands work before local secrets exist:

```powershell
docker compose config
docker compose run --rm bot pzp-bot doctor
```

For real polling, create `.env`, set `TELEGRAM_BOT_TOKEN`, `BOT_ADMINS` and provider settings, then run:

```powershell
docker compose up --build
```

The compose file mounts `./data` at `/app/data` so SQLite and Markdown memory survive restarts.
GitHub Actions installs the same uv 0.12.0 release, runs `uv lock --check`, then uses `uv sync --frozen --extra dev`. Ordinary CI and Docker builds therefore fail on project/lock drift instead of rewriting the lock; dependency upgrades must deliberately refresh and review `uv.lock` in their own change.

## Runtime Checklist

- Run `pzp-bot migrate` before starting a new database.
- Run `pzp-bot doctor` after changing secrets, provider profiles, database paths or memory paths.
- For webhook mode, configure the orchestrator's runtime readiness/liveness probe against `GET /health` and investigate any HTTP 503 before routing Telegram traffic.
- For polling mode, monitor process state plus `background_task_iteration_failed` and `background_task_stopped_unexpectedly` structured events because polling does not expose an HTTP health route.
- Keep `.env`, SQLite files and generated memory out of git.
- Use [operations.md](operations.md) for audit, backup, integrity and maintenance commands.
