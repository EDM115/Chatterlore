# Deployment

## Polling

Polling is the easiest runtime for development:
```powershell
uv run pzp-bot polling
```

Polling clears any configured Telegram webhook first because Telegram update modes are mutually exclusive.

## Webhook

Set:
```env
RUNTIME_MODE=webhook
WEBHOOK_URL=https://example.com/telegram/webhook
WEBHOOK_SECRET=<random-secret>
```

Then run:
```powershell
uv run pzp-bot webhook
```

The FastAPI endpoint rejects requests whose `X-Telegram-Bot-Api-Secret-Token` header does not match `WEBHOOK_SECRET`.

## Docker

```powershell
docker compose up --build
```

Mount `./data` as a persistent volume so SQLite and Markdown memory survive restarts.
