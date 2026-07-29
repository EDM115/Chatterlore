# Setup

This guide gets a local PZP AI instance ready for development, offline verification and first Telegram testing.

## Requirements

- Python 3.12
- [uv](https://docs.astral.sh/uv/) for dependency management and command execution
- A Telegram bot token from BotFather for live Telegram use
- Docker Desktop if you want to run the container smoke checks

## Local Development

```powershell
uv sync --extra dev --extra local
Copy-Item .env.example .env
uv run pzp-bot migrate
uv run pzp-bot doctor
uv run pytest
```

The default `AI_PROVIDER=fake` is deterministic and does not call external AI services. Use it for local development, CI and documentation examples.

For an OpenAI-backed run, keep the key out of committed files and select the model plus reasoning effort explicitly:

```dotenv
AI_PROVIDER=openai_compatible
OPENAI_API_KEY=
OPENAI_BASE_URL=https://api.openai.com/v1
CHAT_MODEL=gpt-5.6-terra
REASONING_LEVEL=medium
EMBEDDING_MODEL=text-embedding-3-small
```

`CHAT_MODEL` and `REASONING_LEVEL` provide environment defaults. Bot admins can replace them through the safe database-backed `chat_model` and `reasoning_level` runtime settings, per-scope `/settings`, or the documented one-shot `/ask` and `/summarize_now` options. For recognized OpenAI reasoning-model IDs, the OpenAI-compatible provider forwards the selected level as the Chat Completions `reasoning_effort` parameter; the prompt and usage metadata retain the same effective value.

## Telegram Preparation

1. Create a bot with BotFather and set `TELEGRAM_BOT_TOKEN` in `.env`.
2. Add the bot to the target supergroup.
3. In BotFather, run `/setprivacy`, choose the bot and select `Disable` if the bot must learn ordinary non-command group messages. Making the bot a group admin is the other practical way to receive those updates.
4. Set `BOT_ADMINS` to the Telegram user IDs that should administer every scope.
5. Start polling with `uv run pzp-bot polling` or use the Docker/Compose flow in [deployment.md](deployment.md).
6. In Telegram, run `/allow_chat` for the whole chat or `/allow_topic` inside a forum topic before expecting live learning.

Telegram does not expose arbitrary historical group backfill through the Bot API. PZP AI learns live updates after consent is enabled and can import reviewed Telegram Desktop exports or Bot API update dumps locally with `pzp-bot import-telegram-json`.

## First Verification

Run these after configuration changes:

```powershell
uv run pzp-bot doctor
uv run pzp-bot show-config
uv run pzp-bot memory-status --scope all
```

Then run `/learn_status` and `/ask <question>` in the target Telegram scope to confirm the bot sees the expected chat or topic.
