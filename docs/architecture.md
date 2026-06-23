# Architecture

The bot is split into four boundaries:
- `telegram`: aiogram routers, polling, FastAPI webhook mode, topic scope extraction, and admin checks
- `memory`: policy evaluation, ingestion, summarization, canonical Markdown compaction, retrieval, import/export, per-scope settings, retention pruning, review approvals, and forget confirmation
- `db`: async SQLAlchemy models and repositories over SQLite
- `ai`: provider protocols plus fake, OpenAI-compatible, and local embedding providers

Every learned message belongs to a `Scope(chat_id, thread_id)`. Topic messages use Telegram's `message_thread_id` only when `is_topic_message` is true. Retrieval for `/ask` uses the current scope by default and does not cross chat/topic boundaries.  
Raw messages remain in SQLite with optional chat/topic/source-link metadata. Markdown files contain categorized canonical durable memory with merged source references:
- `data/memory/daily/YYYY-MM-DD/<chat_id>_<thread_id>.md`
- `data/memory/MEMORY.md`
