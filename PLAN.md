# Telegram Group Memory Assistant PoC v1

## Summary
Build a new Python Telegram bot in the empty `PZP-AI` repo. v1 “learning” means **scoped RAG memory**, not model fine-tuning: the bot records allowed messages, extracts/summarizes durable facts into daily Markdown notes, compacts those notes into `MEMORY.md`, embeds memory items, and answers `/ask` from retrieved memory.

Use **Python 3.12 + aiogram 3.29.0 + FastAPI**. `aiogram 3.29.0` supports Telegram Bot API 10.1 as of 2026-06-14, including current topic/rich-message API coverage. Telegram topic support must use `message_thread_id` and `is_topic_message`.

Important Telegram constraint: the Bot API does not provide full historical group backfill. v1 learns live updates plus manual imports from local export/JSONL files. For full group reading, BotFather privacy mode must be disabled or the bot must be group admin.

## Stack And Project Shape
- Package manager: `uv`; app package under `src/pzp_ai_bot`.
- Core dependencies: `aiogram==3.29.0`, `fastapi`, `uvicorn`, `pydantic-settings`, `sqlalchemy[asyncio]`, `aiosqlite`, `alembic`, `httpx`, `openai`, `numpy`, `typer`, `rich`, `structlog`, `orjson`.
- Optional local AI extra: `sentence-transformers` for local multilingual embeddings; otherwise use cloud embeddings or deterministic fake embeddings.
- Test/quality dependencies: `pytest`, `pytest-asyncio`, `respx`, `freezegun`, `pytest-cov`, `ruff`, `mypy`.
- Suggested files:
  - `src/pzp_ai_bot/main.py`: Typer CLI entrypoint.
  - `src/pzp_ai_bot/telegram/`: aiogram routers, polling runner, webhook runner.
  - `src/pzp_ai_bot/memory/`: ingestion, summarization, retrieval, Markdown compaction.
  - `src/pzp_ai_bot/ai/`: provider interfaces and fake/openai-compatible/local providers.
  - `src/pzp_ai_bot/db/`: SQLAlchemy models, repositories, Alembic migrations.
  - `data/memory/MEMORY.md`, `data/memory/daily/YYYY-MM-DD/*.md`: generated runtime memory files.
  - `docs/`: setup, architecture, command reference, privacy, testing, deployment.

## Interfaces And Behavior
- CLI:
  - `pzp-bot polling`: delete webhook then run long polling.
  - `pzp-bot webhook`: set webhook and run FastAPI webhook server.
  - `pzp-bot migrate`: run DB migrations.
  - `pzp-bot summarize-now --scope ...`: compact pending notes.
  - `pzp-bot export-memory --format md|jsonl --scope ...`.
  - `pzp-bot import-jsonl --file ... --chat-id ... --thread-id ...`.
- Config in `.env.example`:
  - `TELEGRAM_BOT_TOKEN`, `BOT_ADMINS=123,456`, `RUNTIME_MODE=polling|webhook`.
  - `WEBHOOK_URL`, `WEBHOOK_SECRET`, `WEBAPP_HOST=0.0.0.0`, `WEBAPP_PORT=8080`.
  - `DB_URL=sqlite+aiosqlite:///data/bot.db`, `MEMORY_DIR=data/memory`.
  - `AI_PROVIDER=fake|openai_compatible|local`, `CHAT_MODEL`, `EMBEDDING_MODEL`.
  - `OPENAI_API_KEY`, `OPENAI_BASE_URL=https://api.openai.com/v1`.
  - `LEARNING_DEFAULT_ENABLED=false`, `REDACT_BEFORE_AI=true`.
- AI provider contracts:
  - `ChatProvider.generate(messages, *, temperature, max_tokens) -> ChatResult`.
  - `EmbeddingProvider.embed(texts: list[str]) -> list[list[float]]`.
  - Implement `FakeProvider` for tests, `OpenAICompatibleProvider`, and `LocalSentenceTransformerEmbeddingProvider`.
  - Cloud embedding recommendation: `text-embedding-3-small`; local default: multilingual small sentence-transformer; chat model remains config-driven.
- Telegram commands:
  - `/ask <question>`: answer from current chat/topic memory by default.
  - `/memory_search <query>`: show top retrieved memory snippets.
  - `/memory_export [scope]`: send Markdown/JSONL export file.
  - `/stats`: counts seen/learned/skipped, scopes, embeddings, notes, AI calls, DB size.
  - `/learn_on`, `/learn_off`, `/learn_status`: bot-admin/chat-admin learning control.
  - `/allow_chat`, `/deny_chat`, `/allow_topic`, `/deny_topic`: scope ACL, callable inside a topic without passing IDs.
  - `/allow_user <id|reply>`, `/deny_user <id|reply>`, `/allow_admins on|off`: author ACL.
  - `/remember <text>`: manually add a durable note.
  - `/summarize_now`: force daily-note and `MEMORY.md` compaction.
  - `/forget scope=this_topic|this_chat|all confirm=<token>`: destructive memory deletion, admin-only, two-step confirmation.
- Scope rules:
  - Every message scope is `(chat_id, message_thread_id|null)`.
  - A message is learnable only if global/chat/topic learning is on, chat/topic ACL permits it, author ACL permits it, it is not from the bot, it is not a command, and it has usable text/caption.
  - Default `/ask` never crosses chat/topic boundaries. Only `BOT_ADMINS` may request `scope=all`.
  - Admin authorization accepts configured `BOT_ADMINS`; chat-admin checks use cached `getChatAdministrators`.

## Data And Memory Design
- SQLite tables:
  - `messages`: Telegram IDs, chat/thread/user IDs, text, normalized text, learned flag, skip reason, timestamps, reply IDs.
  - `knowledge_items`: extracted facts/manual notes/daily summaries/main summaries with source message IDs and scope.
  - `embeddings`: item/message embedding vector blob, provider, model, dimension.
  - `learning_policies`: global/chat/topic enabled states and ACL rules.
  - `bot_events`: admin actions, compactions, provider errors, exports.
  - `ai_usage`: provider/model, token estimates, latency, error status.
- Markdown memory format:
  - `data/memory/daily/YYYY-MM-DD/<chat_id>_<thread_id>.md`: append-only daily summarized notes with source IDs.
  - `data/memory/MEMORY.md`: compact canonical memory grouped by chat/topic/date, with durable facts, decisions, open questions, and source references.
  - Raw chat should stay in SQLite; Markdown should contain summarized facts, not entire raw conversation dumps.
- Learning pipeline:
  - Ingest allowed Telegram update, normalize text, persist raw message, embed it.
  - Buffer per scope; every `N=25` learnable messages or `15 min`, summarize into daily note using provider or deterministic fallback.
  - On `/summarize_now` or scheduled daily job, merge daily notes into `MEMORY.md` without deleting source-backed items.
  - `/ask` retrieves from `knowledge_items`, `MEMORY.md` sections, and recent raw messages; answer includes compact source references.

## Implementation Phases
- Phase 1: Repo bootstrap with `pyproject.toml`, `uv.lock`, package layout, Ruff/Mypy/Pytest config, Dockerfile, `docker-compose.yml`, `.env.example`, and a fake AI provider.
- Phase 2: Implement settings, DB models, repositories, migrations, Markdown memory writer/reader, and deterministic embedding/search utilities.
- Phase 3: Implement aiogram routers for polling and webhook. Webhook must validate Telegram secret token; polling must clear webhook first because Telegram update modes are mutually exclusive.
- Phase 4: Implement ACL/learning policy engine, admin checks, topic-aware scope handling, and message ingestion.
- Phase 5: Implement summarization, `MEMORY.md` compaction, retrieval, `/ask`, `/memory_search`, `/memory_export`, `/stats`, and `/forget`.
- Phase 6: Implement provider plugins, retry/backoff, redaction before external AI calls, token/latency accounting, and graceful degradation when providers are unavailable.
- Phase 7: Write full docs: setup through BotFather, privacy mode/admin requirements, commands, AI provider config, local/offline testing, webhook deployment, memory format, and known Telegram API limitations.

## Test Plan
- Unit tests:
  - Topic extraction from Telegram update fixtures sets `chat_id` and `message_thread_id` correctly.
  - ACL engine permits/denies by chat, topic, user, bot admin, and chat admin.
  - `/learn_off` prevents new memory writes while still counting skipped messages.
  - Daily notes and `MEMORY.md` compaction preserve source IDs and manual `/remember` notes.
  - Retrieval stays scoped by current topic unless global admin scope is requested.
  - Fake provider makes `/ask` deterministic with no network.
  - `/forget` requires confirmation and deletes only requested scope.
- Integration tests:
  - Feed recorded Telegram JSON updates into aiogram dispatcher without contacting Telegram.
  - Mock Bot API with `respx` for `getChatAdministrators`, `sendMessage`, `sendDocument`, `setWebhook`, `deleteWebhook`.
  - FastAPI webhook rejects missing/wrong secret and accepts valid update.
  - CLI import/export round trips JSONL and Markdown.
  - Alembic migrates a fresh SQLite DB.
- Optional live tests:
  - Mark with `pytest -m telegram_live`; require `TELEGRAM_BOT_TOKEN` and test chat/topic IDs.
  - Never run live tests in default CI.

## Runbook
- Local offline:
  - `uv sync`
  - `uv run pzp-bot migrate`
  - `uv run pytest`
  - `AI_PROVIDER=fake uv run pzp-bot polling`
- Useful cloud AI:
  - Set `AI_PROVIDER=openai_compatible`, `OPENAI_API_KEY`, `CHAT_MODEL`, `EMBEDDING_MODEL=text-embedding-3-small`.
  - Run polling locally or webhook on a VPS with HTTPS.
- Webhook:
  - Set `WEBHOOK_URL=https://domain.example/telegram/webhook`, `WEBHOOK_SECRET=<random>`.
  - `uv run pzp-bot webhook`
- Telegram setup:
  - Create bot with BotFather.
  - Disable privacy mode or add bot as group admin if it must learn non-command group messages.
  - Add bot to target supergroups; use `/allow_chat`, then run `/allow_topic` inside allowed topics.

## Resources And Defaults
- Cloud-model profile: 1 vCPU, 512MB-1GB RAM, SQLite volume, external AI API key.
- Local embedding profile: 2 vCPU, 2-4GB RAM, model cache volume.
- Local chat model profile: 8-16GB RAM or GPU depending on model; use through provider interface, not required for v1 tests.
- Storage: start with 1-5GB for SQLite and Markdown exports; add retention settings after PoC.
- Defaults: learning disabled until explicitly enabled, no cross-chat answers, raw logs redacted, fake provider in tests, polling for local dev, webhook for deploy.

## References
- Telegram Bot API 10.1 recent changes: https://core.telegram.org/bots/api
- Telegram update modes and 24h update retention: https://core.telegram.org/bots/api#getting-updates
- Telegram `message_thread_id` / topic fields: https://core.telegram.org/bots/api#message
- Telegram bot privacy mode: https://core.telegram.org/bots/features#privacy-mode
- aiogram 3.29.0 Bot API 10.1 support: https://docs.aiogram.dev/en/latest/changelog.html
- OpenAI embeddings guide: https://platform.openai.com/docs/guides/embeddings
- Behavior inspiration inspected from `lyzcoote/go-shitfuckllm`: https://github.com/lyzcoote/go-shitfuckllm
