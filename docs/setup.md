# Setup

## Local development

```powershell
uv sync --extra dev
Copy-Item .env.example .env
uv run pzp-bot migrate
uv run pytest
```

The default `AI_PROVIDER=fake` does not call external services and is suitable for tests and offline development.

## Telegram preparation

1. Create a bot with BotFather and set `TELEGRAM_BOT_TOKEN`.
2. Add the bot to the target supergroup.
3. In BotFather, run `/setprivacy`, choose the bot and select `Disable` if it must read ordinary non-command group messages; alternatively make the bot a group admin so Telegram delivers those updates.
4. In the chat, run `/allow_chat`. In a forum topic, run `/allow_topic`.

Telegram does not expose arbitrary historical group backfill through the Bot API. This bot learns live updates and supports manual JSONL imports plus reviewed Telegram Desktop exports or Bot API update dumps through `pzp-bot import-telegram-json`.
