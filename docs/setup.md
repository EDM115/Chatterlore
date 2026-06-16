# Setup

## Local development

```powershell
uv sync --extra dev
Copy-Item .env.example .env
uv run pzp-bot migrate
uv run pytest
```

The default `AI_PROVIDER=fake` does not call external services and is suitable for tests
and offline development.

## Telegram preparation

1. Create a bot with BotFather and set `TELEGRAM_BOT_TOKEN`.
2. Add the bot to the target supergroup.
3. Disable BotFather privacy mode, or make the bot an admin, if it must read ordinary
   non-command group messages.
4. In the chat, run `/allow_chat`. In a forum topic, run `/allow_topic`.

Telegram does not expose arbitrary historical group backfill through the Bot API. This bot
learns live updates and supports manual JSONL imports.
