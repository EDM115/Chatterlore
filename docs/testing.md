# Testing

Default tests are offline:
```powershell
uv run pytest
uv run ruff check .
uv run ty check
```

Phase 3 provider hardening coverage lives in `tests/test_phase3.py`: OpenAI-compatible retry/redaction payloads, local optional-dependency failure messaging, provider-profile fail-closed behavior, CLI doctor diagnostics, embedding usage metadata, insufficient-context answers and prompt/model/reasoning usage metadata.  
Live Telegram tests, when added, must be marked `telegram_live` and require explicit credentials. They should not run in default CI.  
Use `AI_PROVIDER=fake` for deterministic no-network test behavior.
