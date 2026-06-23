# Testing

Default tests are offline:
```powershell
uv run pytest
uv run ruff check .
uv run ty check
```

Phase 3 provider hardening coverage lives in `tests/test_phase3.py`: OpenAI-compatible retry/redaction payloads, local optional-dependency failure messaging, provider-profile fail-closed behavior, CLI doctor diagnostics, embedding usage metadata, insufficient-context answers and prompt/model/reasoning usage metadata.  
Phase 4 Telegram integration coverage lives in `tests/test_phase4_telegram.py`: aiogram dispatcher-fed command and normal-message updates, mocked Bot API methods, valid webhook dispatch, polling startup sequencing, command matrix coverage, ingestion edge cases, Telegram API failure/rate-limit handling and export chunking.  
Optional live Telegram smoke coverage lives in `tests/test_telegram_live.py`. It is marked `telegram_live`, skipped unless `TELEGRAM_LIVE_BOT_TOKEN` and `TELEGRAM_LIVE_CHAT_ID` are set, and should not run in default CI. Run it explicitly with `uv run pytest -m telegram_live tests/test_telegram_live.py`.  
Use `AI_PROVIDER=fake` for deterministic no-network test behavior.
Phase 5 operations coverage lives in `tests/test_phase5.py` and `tests/test_cli.py`: persisted forget confirmations across context restart, expired confirmation rejection, runtime settings refresh/audit events, audit scope filtering and SQLite maintenance helpers/CLI commands. Docker and CI verification are deferred on this workstation until Docker is fully installed.