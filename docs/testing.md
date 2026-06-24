# Testing

Default tests are offline:

```powershell
uv run pytest
uv run ruff check .
uv run ty check
```

Phase 3 provider hardening coverage lives in `tests/test_phase3.py`: OpenAI-compatible retry/redaction payloads, local optional-dependency failure messaging, provider-profile fail-closed behavior, CLI doctor diagnostics, embedding usage metadata, insufficient-context answers and prompt/model/reasoning usage metadata.  
Phase 4 Telegram integration coverage lives in `tests/test_phase4_telegram.py`: aiogram dispatcher-fed command and normal-message updates, mocked Bot API methods, valid webhook dispatch, polling startup sequencing, command matrix coverage, ingestion edge cases, `/settings` view/edit/preview/confirm/reject paths, Telegram API failure/rate-limit handling and export chunking.  
Phase 5 operations coverage lives in `tests/test_phase5.py`, `tests/test_cli.py`, `tests/test_admin_service.py` and `tests/test_deployment_artifacts.py`: persisted forget confirmations across context restart, expired confirmation rejection, runtime settings refresh/audit events, audit scope filtering, configurable admin-cache TTL/stats, SQLite maintenance helpers/CLI commands, successful CLI import/export/summarize/show-config paths, Dockerfile structure and GitHub Actions gate coverage.  
Phase 6 product-completeness coverage lives in `tests/test_phase6.py`, `tests/test_phase4_telegram.py` and `tests/test_cli.py`: scoped memory status and topic discovery, skipped-message diagnostics, Telegram Desktop JSON import, Bot API update dump import, representative import/export/forget memory quality, memory item browse/delete/refresh, `/settings` preview/confirmation, document metadata ingestion and compact source references.  
Optional live Telegram smoke coverage lives in `tests/test_telegram_live.py`. It is marked `telegram_live`, skipped unless `TELEGRAM_LIVE_BOT_TOKEN` and `TELEGRAM_LIVE_CHAT_ID` are set, and should not run in default CI. Run it explicitly with `uv run pytest -m telegram_live tests/test_telegram_live.py`.  
Use `AI_PROVIDER=fake` for deterministic no-network test behavior.

## Docker Smoke

```powershell
docker version
docker compose config
docker build --tag pzp-ai:phase-0.11 .
docker run --rm -e AI_PROVIDER=fake -e DB_URL=sqlite+aiosqlite:////tmp/pzp/bot.db -e MEMORY_DIR=/tmp/pzp/memory pzp-ai:phase-0.11 pzp-bot doctor
docker run --rm -e AI_PROVIDER=fake -e DB_URL=sqlite+aiosqlite:////tmp/pzp/bot.db -e MEMORY_DIR=/tmp/pzp/memory pzp-ai:phase-0.11 pzp-bot migrate
docker compose run --rm bot pzp-bot doctor
docker compose down
```

These commands do not start polling and do not connect to Telegram.

## CI

`.github/workflows/ci.yml` runs GitHub Actions quality gates on pushes and pull requests to `main`: `uv sync --extra dev`, format check, lint, `ty`, pytest, Alembic upgrade, CLI help, `doctor` and a Docker build/run smoke. The optional `telegram_live` test is intentionally excluded from default CI.