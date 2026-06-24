# Architecture

The bot is split into four boundaries:

- `telegram`: aiogram routers, dispatcher composition, polling, FastAPI webhook mode, topic scope extraction, admin checks, safe Telegram API send wrappers, export document chunking, memory status/debug/item maintenance commands and Telegram-native settings commands
- `memory`: policy evaluation, ingestion, summarization, canonical Markdown compaction, retrieval, import/export, Telegram JSON import, manual note metadata, per-scope settings, cross-chat profiles/permissions, summary templates, system prompt templates, bounded range summaries, retention pruning, review approvals, provider/model selection, usage telemetry and forget confirmation
- `db`: async SQLAlchemy models and repositories over SQLite
- `ai`: provider protocols plus fake, OpenAI-compatible, and local embedding providers, provider profile selection, retry/backoff and configurable redaction

Every learned message belongs to a `Scope(chat_id, thread_id)`. Topic messages use Telegram's `message_thread_id` only when `is_topic_message` is true. Retrieval for `/ask` uses the current scope by default. Cross-chat retrieval only happens when a named memory profile is requested and the destination scope has explicit source-scope permission for learning-enabled source scopes.  
Raw messages remain in SQLite with optional chat/topic/source-link metadata. Knowledge items can carry JSON metadata for manual note tags/priority/source and range-summary template/date intervals. `ai_usage.metadata_json` records embedding counts/dimensions, prompt versions, provider profiles, selected models and reasoning levels. Scope settings can override retrieval top-k, summary batch size, summary/chat/embedding models, provider profile, reasoning level, system prompt template, retention and review-before-compact behavior. Markdown files contain categorized canonical durable memory with merged source references:

- `data/memory/daily/YYYY-MM-DD/<chat_id>_<thread_id>.md`
- `data/memory/MEMORY.md`

Provider profiles are configured from the default env vars plus optional `AI_PROVIDER_PROFILES` JSON. Missing OpenAI-compatible credentials fail closed before provider calls. Local embeddings fail with an actionable `uv sync --extra local` message when `sentence-transformers` is not installed.  
Polling and webhook runtimes share `build_dispatcher(context)`, which lets tests feed recorded Telegram JSON updates through aiogram without network. Telegram outbound calls are isolated behind an aiogram `BaseSession` in tests. User-facing sends go through `safe_answer` and `safe_answer_document`, which log `TelegramAPIError` and `TelegramRetryAfter` without crashing update processing. Memory exports are sent as documents and split below Telegram document upload limits when needed.  
Admin checks cache get_chat_administrators results for ADMIN_CACHE_TTL_SECONDS and expose cache stats for diagnostics. Docker and CI artifacts are first-class runtime boundaries: the image runs non-root with a doctor healthcheck and GitHub Actions runs local quality gates plus Docker smoke.
