# Testing

Default tests are offline:
```powershell
uv run pytest
uv run ruff check .
uv run ty check
```

Live Telegram tests, when added, must be marked `telegram_live` and require explicit credentials. They should not run in default CI.  
Use `AI_PROVIDER=fake` for deterministic no-network test behavior.
