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

## Docker

The Docker image uses a multi-stage `python:3.12-slim` build, installs the app into a virtualenv, runs as the non-root `pzp` user and includes a `pzp-bot doctor` healthcheck.

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

## Runtime Checklist

- Run `pzp-bot migrate` before starting a new database.
- Run `pzp-bot doctor` after changing secrets, provider profiles, database paths or memory paths.
- Keep `.env`, SQLite files and generated memory out of git.
- Use [operations.md](operations.md) for audit, backup, integrity and maintenance commands.
