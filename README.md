# PZP AI Telegram Memory Bot

A Python Telegram bot PoC that learns scoped group memory from live messages and answers questions with retrieval-augmented context. The v1 implementation stores raw messages in SQLite, writes summarized Markdown notes, compacts them into `MEMORY.md`, and keeps chat/topic scope boundaries by default.  
See `PLAN.md` and the files in `docs/` for setup, commands, privacy notes, and deployment.

## Quick Start

```powershell
uv sync --extra dev,local
Copy-Item .env.example .env
uv run pzp-bot migrate
uv run pytest
AI_PROVIDER=fake uv run pzp-bot polling
uv run ruff format
uv run ruff check --fix
uv run ty check
```

The fake provider is deterministic and does not call the network. To learn ordinary group messages, disable BotFather privacy mode or make the bot an admin in the group.
