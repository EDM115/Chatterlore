---
goal: Telegram Group Memory Assistant PoC v1
version: 0.11
date_created: 2026-06-16
last_updated: 2026-06-24
owner: PZP-AI
status: In progress
tags:
  - telegram
  - ai
  - memory
  - rag
  - python
  - poc
---

# Telegram Group Memory Assistant PoC v1

![Status: In progress](https://img.shields.io/badge/status-In%20progress-yellow)  
This is the living North Star for the Telegram group memory assistant. Keep it updated after every meaningful development pass. It must describe what exists, what is partial, what remains and what must be true before the project can be considered a working v1.  
The bot learns scoped group memory from live Telegram messages. v1 learning means scoped RAG memory, not model fine-tuning: record allowed messages, summarize durable knowledge, store embeddings, compact Markdown memory and answer questions from the current chat or forum-topic scope by default.  
The plan version evolves independently of the pyproject version. Whenever we move to a new Phase, we bump the minor of the pyproject, with patch evolving as bugfixes inside that phase arrive. v1 in the pyproject will be reached when all 6 phases are complete.

## 1. Product Requirements And Constraints

- **REQ-001**: Learn only from Telegram messages that pass explicit chat/topic/user policy
- **REQ-002**: Treat every message as scoped by `(chat_id, message_thread_id | null)`
- **REQ-003**: Use `message_thread_id` only for Telegram forum-topic messages where `is_topic_message` is true
- **REQ-004**: Keep `/ask` retrieval scoped to the current chat/topic by default
- **REQ-005**: Allow configured bot admins to export, forget, or query all memory
- **REQ-006**: Allow Telegram chat admins to administer their chat/topic scope
- **REQ-007**: Support polling for local/dev use and webhook mode for deployment
- **REQ-008**: Store raw messages in SQLite and summarized durable knowledge in Markdown
- **REQ-009**: Support provider-agnostic AI through explicit chat and embedding protocols
- **REQ-010**: Provide deterministic offline behavior through a fake provider
- **REQ-011**: Provide import/export workflows so development does not depend on Telegram historical backfill
- **REQ-012**: Document setup, privacy constraints, commands, deployment and testing
- **REQ-013**: Support controlled cross-chat learning between explicitly selected channels, groups and forum topics
- **REQ-014**: Allow admins to summarize a bounded message range between two Telegram messages, with optional reusable summary templates
- **REQ-015**: Allow admins to customize prompt templates, select configured AI providers, switch models and tune reasoning level without code changes
- **REQ-016**: Support reloadless runtime configuration and Telegram-native settings management for admins

- **CON-001**: Telegram Bot API does not provide arbitrary full historical group backfill. v1 learns live updates plus manual JSONL imports
- **CON-002**: Reading non-command group messages requires BotFather privacy mode disabled or the bot running as a group admin
- **CON-003**: Default learning must remain disabled unless explicitly enabled
- **CON-004**: Use `uv`, `ruff`, `ty` and `pytest`. Do not add `mypy`
- **CON-005**: Keep tests offline by default. Any live Telegram tests must be explicitly marked `telegram_live`

- **SEC-001**: Never commit `.env`, raw runtime data, SQLite DBs, or generated memory
- **SEC-002**: Reject Telegram webhook calls with a missing or wrong secret token
- **SEC-003**: Redact secret-like values before sending prompts to external providers
- **SEC-004**: Require two-step confirmation for destructive forget operations
- **SEC-005**: Add retention, audit and privacy controls before real group rollout

## 2. Current Architecture

### Runtime And Packaging

- **DONE**: `pyproject.toml` defines Python 3.12 package `pzp-ai`
- **DONE**: `uv.lock` exists and dependencies are pinned
- **DONE**: Runtime dependencies include `aiogram`, `fastapi`, `uvicorn[standard]`, `sqlalchemy[asyncio]`, `aiosqlite`, `alembic`, `openai`, `numpy`, `typer`, `rich`, `structlog` and `orjson`
- **DONE**: Dev dependencies include `pytest`, `pytest-asyncio`, `pytest-cov`, `respx`, `freezegun`, `httpx2`, `ruff` and `ty`
- **DONE**: Optional local embedding extra includes `sentence-transformers`
- **DONE**: `pzp-bot` console script points to `pzp_ai_bot.main:app`
- **DONE**: `.env.example`, `.gitignore`, `.dockerignore`, multi-stage non-root `Dockerfile`, `docker-compose.yml` and `.github/workflows/ci.yml` exist

### Source Layout

- **DONE**: `src/pzp_ai_bot/main.py` provides the Typer CLI
- **DONE**: `src/pzp_ai_bot/config.py` loads environment settings
- **DONE**: `src/pzp_ai_bot/app_context.py` composes settings, DB sessions, providers, memory service and admin service
- **DONE**: `src/pzp_ai_bot/ai/` defines provider protocols and fake/OpenAI/local provider implementations
- **DONE**: `src/pzp_ai_bot/db/` defines async SQLAlchemy models, session helpers and repositories
- **DONE**: `src/pzp_ai_bot/memory/` defines scopes, policy evaluation, vectors, Markdown persistence, retrieval and memory orchestration
- **DONE**: `src/pzp_ai_bot/telegram/` defines admin checks, utility parsing, command router, polling runtime and webhook runtime
- **DONE**: `alembic/versions/0001_initial.py` creates the initial SQLite schema, `alembic/versions/0002_phase2_memory_quality.py` adds Phase 2 metadata/settings/review tables, `alembic/versions/0003_phase2_remaining_memory_tools.py` adds manual-note metadata, profiles, permissions and summary templates, `alembic/versions/0004_phase3_provider_ai_hardening.py` adds provider/prompt settings and AI usage metadata, `alembic/versions/0005_phase5_operations_settings.py` adds runtime settings and persisted forget challenges, and `alembic/versions/0006_phase6_product_completeness.py` adds persisted settings confirmations.
- **DONE**: `docs/` contains setup, architecture, command, privacy, deployment, operations, secrets, release and test documentation
- **DONE**: `tests/` contains offline unit/integration coverage for core behavior

## 3. Implemented Behavior

### CLI

- **DONE**: `pzp-bot migrate` applies Alembic migrations to `head` and records `alembic_version`
- **DONE**: `pzp-bot polling` builds context, deletes Telegram webhook and starts polling
- **DONE**: `pzp-bot webhook` builds context, sets Telegram webhook and starts FastAPI
- **DONE**: `pzp-bot summarize-now --scope <scope>` summarizes pending messages and compacts `MEMORY.md`
- **DONE**: `pzp-bot export-memory --format md|jsonl --scope <scope> [--out path]` exports memory and rejects unsupported formats or invalid scopes before opening the app context
- **DONE**: `pzp-bot import-jsonl --file path --chat-id id [--thread-id id]` imports knowledge items into a scope and rejects empty or malformed JSONL with clear errors
- **DONE**: `pzp-bot show-config` prints non-secret runtime settings, provider profiles, default system prompt and reasoning level
- **DONE**: `pzp-bot doctor` checks configured provider profiles, missing OpenAI-compatible API keys, missing local optional dependencies, SQLite parent write access and memory directory write access
- **DONE**: `pzp-bot audit-events --limit N [--event-type name] [--scope scope] [--format text|jsonl] [--out path]` reviews or exports recent `bot_events`
- **DONE**: `pzp-bot runtime-settings [--reload] [--set key=value]...` views, reloads or edits validated safe database-backed runtime settings
- **DONE**: `pzp-bot db-stats`, `db-integrity`, `db-vacuum` and `db-backup --out path` provide SQLite stats, integrity check, vacuum and online backup maintenance commands

### Telegram Commands

- **DONE**: `/ask [profile=name] [provider=name] [system=name] [model=name] [reasoning=low|medium|high] [prompt="one-shot system prompt"] <question>` answers from current scope memory by default, can opt into explicitly permissioned cross-chat profiles, supports admin-only one-shot provider/model/reasoning/system-prompt overrides and includes compact source references when the provider output does not already cite them
- **DONE**: `/memory_search [profile=name] [provider=name] [embedding_model=name] <query>` returns formatted retrieval results with the same optional memory-profile expansion and admin-only query embedding provider/model overrides
- **DONE**: `/memory_export [md|jsonl] [scope=...]` sends a Markdown or JSONL export with scope/source metadata where available and rejects unknown tokens
- **DONE**: `/stats` returns global message, learned, skipped, knowledge, embedding, event and pending-review counts; `/stats scope=...` and `/memory_status` return richer per-scope message, memory and AI usage status
- **DONE**: `/learn_on`, `/learn_off`, `/learn_status` control/report current scope and show effective memory settings
- **DONE**: `/allow_chat`, `/deny_chat` enable/disable the chat-level scope
- **DONE**: `/allow_topic`, `/deny_topic` enable/disable the current topic scope
- **DONE**: `/allow_user <id|reply>` and `/deny_user <id|reply>` update author policy
- **DONE**: `/allow_admins on|off` stores admin-author learning policy
- **DONE**: `/remember [tags=a,b] [priority=N] [source=url] <text>` creates a manual durable memory note with structured metadata
- **DONE**: `/summarize_now [template=name] [provider=name] [model=name] [reasoning=low|medium|high] [prompt="one-shot instruction"]` summarizes pending messages for the current scope with built-in/custom summary templates plus admin-only provider/model/reasoning/one-shot prompt overrides
- **DONE**: `/memory_settings` directly edits scoped top-k, summary batch size, summary/chat/embedding models, provider profile, reasoning level, system prompt template, retention and review-before-compact settings; `/settings` previews scoped changes and applies them only after confirmation
- **DONE**: `/memory_prune` applies configured retention pruning for a scope
- **DONE**: `/memory_reviews`, `/memory_approve` and `/memory_reject` support review-before-compact workflows
- **DONE**: `/memory_notes`, `/memory_note_edit` and `/memory_note_delete` list, edit and delete manual notes
- **DONE**: `/memory_profiles`, `/memory_profile_set` and `/memory_profile_allow` define named source-scope profiles and explicit destination permissions
- **DONE**: `/memory_templates` and `/memory_template_set` list built-in templates and manage custom summary templates
- **DONE**: `/system_prompts` and `/system_prompt_set` list built-in answer system prompts and manage custom system prompts
- **DONE**: `/summarize_range start=<message_id> end=<message_id> [template=name] [provider=name] [model=name] [prompt="one-shot instruction"]` summarizes a bounded learned message range and records source IDs, date interval, template and AI settings metadata
- **DONE**: `/forget scope=this_topic|this_chat|all confirm=<token>` uses persisted expiring two-step confirmation and deletes scoped memory
- **DONE**: `/audit_events [scope=this_topic|this_chat|all] [event_type=name] [limit=N]` exposes recent `bot_events` for admin review
- **DONE**: `/runtime_settings [reload=on] [key=value ...]` views, reloads or edits validated safe global runtime defaults from Telegram
- **DONE**: `/memory_scopes`, `/memory_debug`, `/memory_items`, `/memory_item_delete` and `/memory_item_refresh` provide topic/scope discovery, skipped-message diagnostics, memory browsing, item deletion and item refresh workflows from Telegram
- **DONE**: `/help` returns Telegram-native command discovery text

### Persistence And Memory

- **DONE**: SQLite tables exist for `messages`, `knowledge_items`, `embeddings`, `learning_policies`, `scope_metadata`, `scope_settings`, `memory_review_items`, `memory_profiles`, `memory_profile_scopes`, `memory_scope_permissions`, `summary_templates`, `system_prompt_templates`, `runtime_settings`, `forget_challenges`, `settings_challenges`, `bot_events` and `ai_usage`
- **DONE**: Raw messages are stored with Telegram IDs, chat/thread/user IDs, optional chat/topic/source-link metadata, text, normalized text, learned flag, skip reason, reply ID and timestamps
- **DONE**: Knowledge items store kind, content, scope, source message IDs, JSON metadata and timestamps
- **DONE**: Embeddings store target type, target ID, scope, provider, model, dimensions, vector blob and timestamp
- **DONE**: Learning policies store global/chat/topic/user/admin toggles
- **DONE**: AI usage records ask, summarize, range-summary, canonical-compaction and embedding provider/model/tokens/latency/error plus JSON metadata for provider profile, selected models, prompt/template versions, reasoning level and embedding count/dimensions
- **DONE**: `data/memory/MEMORY.md` is canonically compacted from knowledge items into scoped Facts, Decisions, Preferences, Open Questions and Todos sections with deduplicated sources
- **DONE**: `data/memory/daily/YYYY-MM-DD/<chat_id>_<thread_id>.md` daily files are appended for manual, import and summary memory
- **DONE**: `FakeProvider` gives deterministic chat, canonical compaction and embedding behavior
- **DONE**: `OpenAICompatibleProvider` supports configurable chat and embedding calls
- **DONE**: `LocalSentenceTransformerEmbeddingProvider` supports optional local embeddings when the `local` extra is installed
- **DONE**: Retrieval uses embedding cosine similarity over knowledge-item embeddings first and learned raw-message embeddings as the second tier
- **DONE**: Cross-chat retrieval is disabled by default and only expands through named profiles whose source scopes have destination permission and enabled learning policy
- **DONE**: `/ask` falls back to deterministic scoped results if chat generation fails
- **DONE**: Duplicate Telegram message ingestion no-ops deterministically and does not duplicate message embeddings
- **DONE**: Global summarization groups pending messages by concrete chat/topic scope instead of creating mixed global knowledge items
- **DONE**: Per-scope memory settings override top-k, summary batch size, summary/chat/embedding models, provider profile, reasoning level, system prompt template, raw-message retention, knowledge retention and review-before-compact defaults
- **DONE**: Retention pruning can remove old raw messages and message embeddings without deleting durable summaries
- **DONE**: Optional review-before-compact mode writes proposed summaries and requires approval before durable knowledge insertion
- **DONE**: Manual notes can be tagged, prioritized, sourced, listed, edited, deleted and exported with metadata
- **DONE**: Bounded range summaries use built-in, custom or one-shot templates and store boundary message IDs, source IDs, date intervals and AI settings metadata
- **DONE**: OpenAI-compatible chat and embedding calls use bounded retry/backoff and configurable redaction for secret-like key/value pairs, bearer/common token forms, emails, phone numbers and custom regex patterns
- **DONE**: Provider profiles can be configured through `AI_PROVIDER_PROFILES`; missing OpenAI-compatible credentials fail closed and local embedding missing extras raise actionable install guidance
- **DONE**: `/ask` returns an explicit insufficient-context answer when scoped memory is empty or below the configured score threshold

### Telegram Runtime

- **DONE**: Polling runtime calls `delete_webhook` before `start_polling`
- **DONE**: Webhook runtime sets the webhook on lifespan startup
- **DONE**: Webhook endpoint validates `X-Telegram-Bot-Api-Secret-Token`
- **DONE**: Topic scope extraction uses `is_topic_message` and `message_thread_id`
- **DONE**: Admin service caches `get_chat_administrators` results, honors configured `BOT_ADMINS`, supports configurable `ADMIN_CACHE_TTL_SECONDS` and exposes cache stats for diagnostics
- **DONE**: Polling and webhook runtimes start a background idle summarizer using `SUMMARY_IDLE_SECONDS`
- **DONE**: Polling and webhook startup register the current Telegram command list through `set_my_commands`
- **DONE**: Polling and webhook runtimes start a background runtime-settings refresher using `RUNTIME_SETTINGS_REFRESH_SECONDS`
- **DONE**: Telegram update handling uses structured `structlog` logging with safe chat/thread/user/message metadata, duration and error fields

### Documentation

- **DONE**: `README.md` contains a quick-start runbook
- **DONE**: `docs/setup.md` documents local setup and Telegram preparation
- **DONE**: `docs/architecture.md` documents package boundaries and memory files
- **DONE**: `docs/commands.md` documents command behavior
- **DONE**: `docs/privacy.md` documents privacy mode, storage, retention, review workflow and provider notes
- **DONE**: `docs/deployment.md` documents polling, webhook and Docker usage
- **DONE**: `docs/operations.md` documents structured logs, audit review, runtime settings and DB maintenance
- **DONE**: `docs/secrets.md` documents `.env`, provider keys, Telegram token/webhook secret rotation and container secret guidance
- **DONE**: `docs/release.md` documents versioning, release checks, migration state and manual Telegram smoke expectations
- **DONE**: `docs/testing.md` documents offline verification commands

### Current Verification Baseline

- **DONE**: `uv sync --extra dev --extra local` passed on 2026-06-24 after plan v0.11
- **DONE**: `uv run ruff format --check .` passed on 2026-06-24 after plan v0.11
- **DONE**: `uv run ruff check .` passed on 2026-06-24 after plan v0.11
- **DONE**: `uv run ty check` passed on 2026-06-24 after plan v0.11
- **DONE**: `uv run pytest` passed on 2026-06-24 with 118 passed, 1 skipped and 4 existing aiosqlite thread warnings after plan v0.11
- **DONE**: `uv run alembic upgrade head` passed on 2026-06-24 through `0006_phase6_product_completeness` after plan v0.11
- **DONE**: `uv run pzp-bot --help` displayed all CLI commands on 2026-06-24 after plan v0.11
- **DONE**: `uv run pzp-bot doctor` passed on 2026-06-24 with the default fake provider after plan v0.11
- **DONE**: Docker runtime verification passed on 2026-06-24 with `docker version`, `docker compose config`, `docker build --tag pzp-ai:phase-0.11 .`, container `pzp-bot doctor`, container `pzp-bot migrate` and `docker compose run --rm bot pzp-bot doctor`
- **DONE**: CI workflow validation passed on 2026-06-24 through static artifact tests; `.github/workflows/ci.yml` runs format, lint, type check, tests, Alembic upgrade, CLI help, doctor and Docker smoke on GitHub Actions
- **DONE**: Full local and Docker verification baseline was re-run on 2026-06-24 after plan v0.11
- **TODO**: Re-run the full verification baseline after every future code or dependency change and update this section with date and result

## 4. Known Partial Implementations

No non-live partial implementations are currently tracked. Live Telegram beta checks remain in Gate C because they require real Telegram connectivity and disposable group administration.

## 5. Remaining Roadmap

### Phase 1 - Close PoC Correctness Gaps

Goal: make existing behavior deterministic, internally consistent and covered by tests.

| Task     | Status | Description                                                                                                                                             | Acceptance Criteria                                                                                                                     |
| -------- | ------ | ------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------- |
| TASK-001 | DONE   | Implement scheduled idle summarization using `SUMMARY_IDLE_SECONDS` in polling and webhook runtimes                                                     | Pending learned messages are summarized after idle timeout without manual command; unit test uses fake clock or direct scheduler hook   |
| TASK-002 | DONE   | Fix global CLI summarization semantics. `summarize-now --scope all` must either summarize each concrete scope separately or reject with a clear message | No global summary item is created from mixed chat/topic messages unless explicitly requested; tests cover all/current/chat/topic        |
| TASK-003 | DONE   | Use Alembic in `pzp-bot migrate` or explicitly rename command to `init-schema`                                                                          | CLI migration path and Alembic migration path are not divergent; test verifies fresh SQLite migration                                   |
| TASK-004 | DONE   | Add idempotent policy upsert behavior that is robust with nullable SQLite unique columns                                                                | Repeated `/learn_on`, `/allow_user` and `/deny_user` update one logical policy row; test verifies no duplicates                         |
| TASK-005 | DONE   | Remove remaining `type: ignore[arg-type]` from CLI/router export paths by narrowing format types at parse time                                          | `uv run ty check` passes with no local ignore for export format typing                                                                  |
| TASK-006 | DONE   | Ensure duplicate Telegram messages update or no-op deterministically                                                                                    | Re-ingesting same `(chat_id, thread_id, message_id)` cannot duplicate raw messages or embeddings; test covers duplicate update delivery |
| TASK-007 | DONE   | Use message embeddings in retrieval as a second tier below knowledge items                                                                              | `/memory_search` can return relevant raw recent messages with source IDs when no summary exists; tests cover ranking and scope          |
| TASK-008 | DONE   | Prevent import/export edge cases for empty files, malformed JSONL, invalid scope and unsupported format                                                 | CLI and service return clear errors; tests cover malformed and empty inputs                                                             |
| TASK-009 | DONE   | Add command help text and optional `/help` command                                                                                                      | Users can discover all commands from Telegram without reading docs                                                                      |
| TASK-010 | DONE   | Add Telegram command registration through `set_my_commands`                                                                                             | Bot registers current command list at startup; test mocks Bot API call                                                                  |

### Phase 2 - Strengthen Memory Quality

Goal: make `MEMORY.md` a useful canonical memory file rather than only a dump of stored knowledge rows.

| Task     | Status | Description                                                                                                                          | Acceptance Criteria                                                                                                                                                                                            |
| -------- | ------ | ------------------------------------------------------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| TASK-011 | DONE   | Implement LLM-assisted canonical compaction from daily summaries into stable facts, decisions, preferences, open questions and todos | `MEMORY.md` is grouped by scope and semantic category; sources are preserved; fake provider test is deterministic                                                                                              |
| TASK-012 | DONE   | Deduplicate semantically similar knowledge items during compaction                                                                   | Repeated facts do not produce repeated bullets; source IDs are merged                                                                                                                                          |
| TASK-013 | DONE   | Add source formatting with Telegram message references where possible                                                                | Memory entries include source IDs and, when chat username/link data exists, clickable Telegram links                                                                                                           |
| TASK-014 | DONE   | Store optional chat title, topic title, username and message permalink metadata                                                      | Exports are readable without raw numeric IDs only; migration and tests cover metadata                                                                                                                          |
| TASK-015 | DONE   | Add per-scope memory settings such as top-k, summary batch size and summary model                                                    | Scope-specific settings override defaults and are visible in `/learn_status`                                                                                                                                   |
| TASK-016 | DONE   | Add retention controls for raw messages and generated knowledge                                                                      | Admins can configure retention; old raw messages can be pruned without deleting durable summaries                                                                                                              |
| TASK-017 | DONE   | Add memory review workflow before compacting sensitive scopes                                                                        | Optional mode writes proposed summaries and requires admin approval before knowledge insertion                                                                                                                 |
| TASK-018 | DONE   | Add richer manual note commands: tags, source, priority and edit/delete note                                                         | `/remember` notes can be listed, edited, deleted and exported with tags                                                                                                                                        |
| TASK-019 | DONE   | Add explicit cross-chat learning scopes for selected source channels, groups and topics                                              | Admin can define a named learning profile such as one channel, two groups and three topics; ingestion keeps original source scope metadata; retrieval can opt into the profile without leaking unrelated chats |
| TASK-020 | DONE   | Add source-scope permissions for cross-chat memory sharing                                                                           | A scope can only learn from or query another scope when both policy and admin permissions allow it; tests cover denied and allowed cross-scope access                                                          |
| TASK-021 | DONE   | Add message-range summarization between two Telegram messages                                                                        | Admin can reply/select/provide two message IDs; the bot checks both message dates and source scope, summarizes only the bounded range and records the date interval and source IDs                             |
| TASK-022 | DONE   | Add reusable summary templates for range and scope summaries                                                                         | Admin can choose configured templates for decisions, todos, meeting notes, incident recap, or custom formats; fake provider tests snapshot the prompt inputs                                                   |

### Phase 3 - Provider And AI Hardening

Goal: make provider failures, privacy and costs explicit and manageable.

| Task     | Status | Description                                                                    | Acceptance Criteria                                                                                                                                                     |
| -------- | ------ | ------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| TASK-023 | DONE   | Apply retry/backoff consistently to OpenAI-compatible chat and embedding calls | Transient provider failures retry with bounded backoff; tests mock transient errors                                                                                     |
| TASK-024 | DONE   | Track embedding AI usage in `ai_usage`                                         | Embedding operations record provider, model, dimensions/count, latency and errors                                                                                       |
| TASK-025 | DONE   | Add provider health checks to `pzp-bot show-config` or a new `doctor` command  | CLI reports missing tokens, unusable local optional deps and DB/memory path health                                                                                      |
| TASK-026 | DONE   | Add tests for OpenAI-compatible provider payloads and redaction                | Tests verify secret-like strings are redacted and request payloads match API expectations                                                                               |
| TASK-027 | DONE   | Add tests for local embedding provider dependency failure messaging            | Running `AI_PROVIDER=local` without local extra fails with a clear actionable message                                                                                   |
| TASK-028 | DONE   | Replace basic redaction with configurable PII/secrets scrubbing                | Redaction covers emails, phone numbers, common token forms and custom patterns                                                                                          |
| TASK-029 | DONE   | Add prompt templates as versioned files or constants with tests                | Summary and answer prompts are reviewable, versioned and snapshot-tested                                                                                                |
| TASK-030 | DONE   | Add answer confidence/insufficient-context behavior                            | `/ask` explicitly says when scoped memory is weak; tests cover empty and low-score retrieval                                                                            |
| TASK-031 | DONE   | Add configurable system prompt templates                                       | Admins can select a named system prompt template per scope or request; prompts are versioned, auditable and included in usage metadata                                  |
| TASK-032 | DONE   | Add on-the-fly prompt override for admin-triggered commands                    | Authorized admins can send a one-shot prompt/template override for a single `/ask` or summary run without changing global defaults                                      |
| TASK-033 | DONE   | Support multiple configured AI providers in bot settings                       | Settings can define more than one provider profile; admins can switch provider profile per scope or per request; missing credentials fail closed with clear diagnostics |
| TASK-034 | DONE   | Allow model and reasoning-level selection                                      | Admins can choose chat model, embedding model where applicable and reasoning level from validated options; choices are reflected in `ai_usage` and `/settings`          |

### Phase 4 - Telegram Integration Coverage

Goal: prove Telegram behavior without requiring live Telegram servers in default tests.

| Task     | Status | Description                                                                                          | Acceptance Criteria                                                                                  |
| -------- | ------ | ---------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------- |
| TASK-035 | DONE   | Feed recorded Telegram JSON updates through aiogram `Dispatcher` in tests                            | Command and normal-message handlers are covered without network                                      |
| TASK-036 | DONE   | Mock Bot API calls with `respx` or aiogram test utilities                                            | Tests cover `getChatAdministrators`, `sendMessage`, `sendDocument`, `setWebhook` and `deleteWebhook` |
| TASK-037 | DONE   | Add valid webhook update test                                                                        | Correct secret plus valid update returns `{"ok": true}` and invokes dispatcher                       |
| TASK-038 | DONE   | Add polling startup test                                                                             | Polling deletes webhook before starting and includes the router                                      |
| TASK-039 | DONE   | Add tests for all Telegram commands                                                                  | Each command has success, invalid-args, unauthorized and scoped behavior tests                       |
| TASK-040 | DONE   | Add tests for topic, non-topic, captions, replies, bot-authored messages, commands and empty content | Ingestion decisions match scope and skip rules                                                       |
| TASK-041 | DONE   | Add optional `telegram_live` smoke tests                                                             | Live tests are skipped by default and document required env vars                                     |
| TASK-042 | DONE   | Add Telegram error handling and rate-limit handling                                                  | Telegram API failures are logged and do not crash update processing                                  |
| TASK-043 | DONE   | Add export chunking or file fallback for large Telegram responses                                    | Large memory exports always use documents and respect Telegram file limits                           |

### Phase 5 - Operations, Observability and Security

Goal: make the bot safe to run for a real group over time.

| Task     | Status   | Description                                                  | Acceptance Criteria                                                                                                                                                  |
| -------- | -------- | ------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| TASK-044 | DONE     | Implement structured logging with `structlog`                | Logs include event type, chat_id, thread_id, user_id when safe, duration and error                                                                                   |
| TASK-045 | DONE     | Add audit views or export for `bot_events`                   | Admin actions and destructive operations can be reviewed                                                                                                             |
| TASK-046 | DONE     | Persist forget confirmation challenges with expiry           | Restart does not create ambiguous destructive state; expired tokens cannot be reused                                                                                 |
| TASK-047 | DONE     | Add DB maintenance commands                                  | CLI supports vacuum, backup, stats and integrity check for SQLite                                                                                                    |
| TASK-048 | DONE     | Add privacy/data-processing documentation for real group use | Docs explain what is stored, sent to providers, retained, exported and deleted                                                                                       |
| TASK-049 | DONE     | Add retention pruning command and policy                     | Admin can prune raw messages older than configured window per scope                                                                                                  |
| TASK-050 | DONE     | Add secrets handling guidance for deployment                 | Docs cover `.env`, Docker secrets, provider keys and Telegram token rotation                                                                                         |
| TASK-051 | DONE     | Add Docker runtime verification                              | Docker build, container `doctor`, container migration and compose `doctor` smoke pass locally without starting Telegram polling                                      |
| TASK-052 | DONE     | Add CI workflow                                              | GitHub Actions runs format, lint, type check, tests, Alembic upgrade, CLI help, doctor and Docker smoke on push and pull request                                    |
| TASK-053 | DONE     | Add release checklist and versioning process                 | Releases specify migration state, config changes and manual Telegram smoke tests                                                                                     |
| TASK-054 | DONE     | Add reloadless runtime settings refresh                      | File/env-backed settings have a validated reload path; long-running polling and webhook runtimes apply non-secret setting changes without process restart where safe |
| TASK-055 | DONE     | Store editable bot settings in the database                  | Runtime settings that are safe to edit are persisted with schema, validation, defaults, scope overrides, audit events and rollback behavior                          |

### Phase 6 - Product Completeness

Goal: make the assistant useful to group admins and participants beyond the minimal PoC.

| Task     | Status | Description                                                                 | Acceptance Criteria                                                                                                                                                           |
| -------- | ------ | --------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| TASK-056 | DONE   | Add a `/memory_status` or richer `/stats` by scope                          | Admin can see learned/skipped/messages/knowledge/AI usage per chat/topic                                                                                                      |
| TASK-057 | DONE   | Add natural-language admin responses that explain why a message was skipped | Admin debug mode can show skip reasons without leaking private data                                                                                                           |
| TASK-058 | DONE   | Add topic allowlist discovery helper                                        | Admin can list known topics/scopes and current learning status                                                                                                                |
| TASK-059 | DONE   | Add manual import from Telegram JSON exports and update dumps               | User can import Telegram Desktop exports or Bot API update dumps locally without Telegram API backfill                                                                        |
| TASK-060 | DONE   | Add memory browsing commands                                                | Admin can list recent notes/summaries and inspect item IDs                                                                                                                    |
| TASK-061 | DONE   | Add item-level deletion                                                     | Admin can delete one memory item without forgetting the entire scope                                                                                                          |
| TASK-062 | DONE   | Add item-level re-embedding/re-summarization                                | Admin can refresh memory after provider/model changes                                                                                                                         |
| TASK-063 | DONE   | Add support for selected non-text message metadata                          | Captions, links, document names and image alt/manual notes can be learned safely                                                                                              |
| TASK-064 | DONE   | Add multilingual prompt and memory behavior guidance                        | Bot handles multilingual chats consistently and docs explain provider choice                                                                                                  |
| TASK-065 | DONE   | Add response source presentation improvements                               | `/ask` answers cite compact source references without overwhelming Telegram messages                                                                                          |
| TASK-066 | DONE   | Add `/settings` admin command                                               | Admins can view and edit validated settings from Telegram, including learning policy defaults, provider profile, model, reasoning level, summary template and prompt template |
| TASK-067 | DONE   | Add settings change previews and confirmation                               | Sensitive or broad changes show a preview, require confirmation and write an audit event before taking effect                                                                 |

## 6. Test Plan

### Tests Implemented

- **DONE**: `tests/test_scope.py` verifies topic scope extraction and scope parsing
- **DONE**: `tests/test_policy_and_ingestion.py` verifies learning-disabled skip, learning-enabled summarization and user deny behavior
- **DONE**: `tests/test_memory_service.py` verifies scoped remember/search/ask, forget confirmation and JSONL import/export
- **DONE**: `tests/test_webhook.py` verifies bad webhook secret rejection
- **DONE**: `tests/test_phase1.py` verifies concrete-scope global summarization, idle summarization, duplicate ingestion idempotency, nullable policy cleanup, message-embedding retrieval and JSONL validation
- **DONE**: `tests/test_phase2.py` verifies canonical compaction grouping/dedup/source links, per-scope settings and review approval, and retention pruning without durable-summary deletion
- **DONE**: `tests/test_phase2_remaining.py` verifies manual note metadata/edit/delete/export, cross-chat profile permission gating, range summaries with templates and fake-provider template prompt inputs
- **DONE**: `tests/test_phase3.py` verifies OpenAI-compatible retry/redaction payloads, local optional-dependency failure messaging, provider-profile fail-closed behavior, CLI doctor diagnostics, embedding usage metadata, insufficient-context answers and prompt/model/reasoning usage metadata
- **DONE**: `tests/test_phase4_telegram.py` verifies aiogram dispatcher-fed command and normal-message updates, mocked Bot API methods, valid webhook dispatch, polling startup sequencing, command matrix coverage, ingestion edge cases, Telegram API failure/rate-limit handling and export document chunking
- **DONE**: `tests/test_telegram_live.py` provides optional skipped-by-default `telegram_live` smoke coverage for real Telegram credentials
- **DONE**: `tests/test_phase5.py` verifies persisted forget confirmations across restarts, expired confirmation rejection, runtime settings refresh/audit events, audit scope filtering, SQLite stats and runtime-settings refresher scheduling
- **DONE**: `tests/test_phase6.py` verifies scoped memory status and topic discovery, skipped-message diagnostics, Telegram Desktop JSON import, Bot API update dump import, representative import/export/forget memory quality, memory item browse/delete/refresh, `/settings` preview/confirmation and compact source references
- **DONE**: `tests/test_cli.py` verifies Alembic-backed migration, CLI validation errors, successful `summarize-now`, `export-memory`, `import-jsonl`, `show-config`, Phase 5 runtime settings/audit export commands and DB maintenance commands
- **DONE**: `tests/test_telegram_commands.py` verifies command help text and mocked `set_my_commands` registration
- **DONE**: `tests/test_admin_service.py` verifies configurable admin-cache TTL, cache reuse and cache stats
- **DONE**: `tests/test_deployment_artifacts.py` verifies Dockerfile, `.dockerignore`, GitHub Actions quality gates and Alembic runtime root discovery
- **DONE**: `tests/conftest.py` builds isolated SQLite and fake-provider context

### Tests Still Required

- **DONE**: TEST-004 successful CLI `summarize-now`, `export-memory`, `import-jsonl` and `show-config` coverage is implemented
- **DONE**: TEST-013 Docker smoke test and documentation are implemented
- **DONE**: TEST-019 `/settings` view, direct edit compatibility, preview, confirm, rejection and unauthorized coverage is implemented
- **TODO**: Optional live Telegram smoke remains skipped by default and requires real credentials

### Required Verification Commands

Run these after every implementation pass:

```powershell
uv sync --extra dev --extra local
uv run ruff format --check .
uv run ruff check .
uv run ty check
uv run pytest
uv run alembic upgrade head
uv run pzp-bot --help
uv run pzp-bot doctor
```

Use this explicit local binary if the PATH shim is broken on this Windows machine:

```powershell
& '%LOCALAPPDATA%\Microsoft\WinGet\Packages\astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe\uv.exe' run pytest
```

## 7. Release Gates

### Gate A - Current PoC Baseline

- **DONE**: Repo has working Python package and lockfile
- **DONE**: Offline tests pass with fake provider
- **DONE**: CLI, memory service, DB, Telegram router, polling and webhook code exist
- **DONE**: Documentation exists for setup, architecture, commands, privacy, deployment and testing
- **DONE**: Gate verified green on 2026-06-23 after Phase 1 implementation

### Gate B - Local Alpha

Required before serious local dogfooding:

- **DONE**: Full verification commands passed on 2026-06-23 from the current checkout
- **DONE**: CLI migration path is aligned with Alembic
- **DONE**: Dispatcher tests cover command behavior
- **DONE**: Scheduled idle summarization works in polling and webhook runtimes and is covered through the direct scheduler hook
- **DONE**: `/stats scope=...` and `/memory_status` report useful per-scope and AI usage information
- **DONE**: Admins can inspect and update safe memory-quality settings without editing files for local dogfooding
- **DONE**: Duplicate update handling is tested
- **DONE**: Provider failure behavior is tested

### Gate C - Private Telegram Beta

Required before inviting a real private group:

- **TODO**: Live Telegram smoke test has passed in a disposable group
- **DONE**: BotFather privacy-mode/admin setup is documented with exact steps in `docs/setup.md`
- **TODO**: BotFather privacy-mode/admin setup is verified in a disposable group
- **DONE**: Forget behavior, retention behavior and destructive-operation audit expectations are documented
- **TODO**: Forget behavior and real-group retention behavior are manually tested in a disposable group
- **DONE**: Export and forget workflows are tested offline with a representative imported-memory dataset
- **DONE**: Admin policy behavior is tested with configured bot admins and mocked Telegram chat admins
- **DONE**: Cross-chat learning is disabled by default and explicitly permissioned for every source and destination scope used in beta
- **DONE**: Logs are structured and do not leak secrets
- **DONE**: Docker deployment smoke verification passes locally without starting Telegram polling

### Gate D - v1 Release

Required before calling this v1 complete:

- **DONE**: All Phase 1, Phase 2, Phase 3, Phase 4, Phase 5 and Phase 6 tasks are complete
- **DONE**: Docker runtime verification is complete locally
- **DONE**: CI workflow runs formatting, linting, type checking, tests, migration checks, CLI checks and Docker smoke on GitHub Actions
- **DONE**: Migration history is stable and tested from an empty database through `0006_phase6_product_completeness`
- **DONE**: Memory quality is covered by a representative imported dataset test using summarize, ask, export and forget
- **DONE**: Privacy and operational documentation is complete
- **DONE**: Manual runbook covers install, upgrade, backup, restore, token rotation, provider switching, prompt/template switching, reloadless settings, `/settings` and emergency forget

## 8. Operational Runbook

### Local Offline

```powershell
uv sync --extra dev --extra local
Copy-Item .env.example .env
uv run pzp-bot migrate
uv run pytest
AI_PROVIDER=fake uv run pzp-bot polling
```


### Docker Smoke

```powershell
docker version
docker compose config
docker build --tag pzp-ai:phase-0.11 .
docker run --rm -e AI_PROVIDER=fake -e DB_URL=sqlite+aiosqlite:////tmp/pzp/bot.db -e MEMORY_DIR=/tmp/pzp/memory pzp-ai:phase-0.11 pzp-bot doctor
docker run --rm -e AI_PROVIDER=fake -e DB_URL=sqlite+aiosqlite:////tmp/pzp/bot.db -e MEMORY_DIR=/tmp/pzp/memory pzp-ai:phase-0.11 pzp-bot migrate
docker compose run --rm bot pzp-bot doctor
docker compose down
```

These commands verify the image and compose runtime without starting Telegram polling.
### Cloud AI Provider

```env
AI_PROVIDER=openai_compatible
OPENAI_API_KEY=<provider-key>
OPENAI_BASE_URL=https://api.openai.com/v1
CHAT_MODEL=<configured-chat-model>
EMBEDDING_MODEL=text-embedding-3-small
```

### Local Embeddings

```powershell
uv sync --extra dev --extra local
```

Then set:

```env
AI_PROVIDER=local
EMBEDDING_MODEL=sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
```

### Webhook

```env
RUNTIME_MODE=webhook
WEBHOOK_URL=https://domain.example/telegram/webhook
WEBHOOK_SECRET=<random-secret>
```

```powershell
uv run pzp-bot webhook
```

### Telegram Setup

1. Create a bot with BotFather
2. Set `TELEGRAM_BOT_TOKEN`
3. Add configured user IDs to `BOT_ADMINS`
4. Disable BotFather privacy mode or make the bot a group admin if it must learn ordinary group messages
5. Add the bot to the target supergroup
6. Run `/allow_chat` in the group
7. Run `/allow_topic` in each forum topic that may be learned
8. Use `/learn_status`, `/stats`, `/remember`, `/ask` and `/memory_export` to verify behavior

### Admin Settings Target

- `/runtime_settings` is the current global safe runtime-settings command from Telegram
- `/settings` is the scoped memory/provider/prompt settings command with preview/confirmation UX; `/memory_settings` remains available for direct local admin edits
- File and environment values remain the bootstrap and secret source of truth
- Non-secret runtime choices such as learning default, provider profile, model, reasoning level, memory top-k, batch sizes, summary cadence and retention defaults are editable without a process restart after validation
- Provider secrets must not be pasted through Telegram unless a future encrypted secret workflow is explicitly designed and documented

## 9. File Ownership Map

- **PROJECT-001**: `pyproject.toml`, `uv.lock`, `.env.example`, `.gitignore`, `.dockerignore`, `README.md`, `Dockerfile`, `docker-compose.yml`, `.github/workflows/ci.yml`
- **RUNTIME-001**: `src/pzp_ai_bot/main.py`, `src/pzp_ai_bot/app_context.py`, `src/pzp_ai_bot/config.py`, `src/pzp_ai_bot/logging.py`
- **TELEGRAM-001**: `src/pzp_ai_bot/telegram/admin.py`, `src/pzp_ai_bot/telegram/commands.py`, `src/pzp_ai_bot/telegram/dispatcher.py`, `src/pzp_ai_bot/telegram/middleware.py`, `src/pzp_ai_bot/telegram/router.py`, `src/pzp_ai_bot/telegram/safe_api.py`, `src/pzp_ai_bot/telegram/scheduler.py`, `src/pzp_ai_bot/telegram/utils.py`, `src/pzp_ai_bot/telegram/polling.py`, `src/pzp_ai_bot/telegram/webhook.py`
- **MEMORY-001**: `src/pzp_ai_bot/memory/service.py`, `src/pzp_ai_bot/memory/prompts.py`, `src/pzp_ai_bot/memory/canonical.py`, `src/pzp_ai_bot/memory/policy.py`, `src/pzp_ai_bot/memory/retrieval.py`, `src/pzp_ai_bot/memory/markdown.py`, `src/pzp_ai_bot/memory/scope.py`, `src/pzp_ai_bot/memory/vector.py`
- **AI-001**: `src/pzp_ai_bot/ai/protocols.py`, `src/pzp_ai_bot/ai/fake.py`, `src/pzp_ai_bot/ai/openai_compatible.py`, `src/pzp_ai_bot/ai/local.py`, `src/pzp_ai_bot/ai/factory.py`, `src/pzp_ai_bot/ai/redaction.py`
- **DB-001**: `src/pzp_ai_bot/db/models.py`, `src/pzp_ai_bot/db/repositories.py`, `src/pzp_ai_bot/db/session.py`, `alembic/env.py`, `alembic/versions/0001_initial.py`, `alembic/versions/0002_phase2_memory_quality.py`, `alembic/versions/0003_phase2_remaining_memory_tools.py`, `alembic/versions/0004_phase3_provider_ai_hardening.py`, `alembic/versions/0005_phase5_operations_settings.py`, `alembic/versions/0006_phase6_product_completeness.py`
- **DOCS-001**: `docs/setup.md`, `docs/architecture.md`, `docs/commands.md`, `docs/privacy.md`, `docs/deployment.md`, `docs/operations.md`, `docs/secrets.md`, `docs/release.md`, `docs/testing.md`
- **TESTS-001**: `tests/conftest.py`, `tests/test_scope.py`, `tests/test_policy_and_ingestion.py`, `tests/test_memory_service.py`, `tests/test_phase1.py`, `tests/test_phase2.py`, `tests/test_phase2_remaining.py`, `tests/test_phase3.py`, `tests/test_phase4_telegram.py`, `tests/test_phase5.py`, `tests/test_phase6.py`, `tests/test_cli.py`, `tests/test_admin_service.py`, `tests/test_deployment_artifacts.py`, `tests/test_telegram_commands.py`, `tests/test_telegram_live.py`, `tests/test_webhook.py`

## 10. Risks And Assumptions

- **RISK-001**: Telegram privacy mode or missing admin permissions can make live testing look broken even when code works
- **RISK-002**: SQLite nullable unique constraints may not enforce logical policy uniqueness as intended without explicit upsert keys
- **RISK-003**: Provider costs and privacy risks can grow quickly if large raw chats are summarized by external models
- **RISK-004**: Current fake provider is useful for deterministic tests but does not prove real answer quality
- **RISK-005**: Current memory compaction performs deterministic heuristic deduplication but can still miss deeper semantic duplicates or keep low-value facts
- **RISK-006**: Runtime scheduling has focused tests, but true long-running polling/webhook behavior can still differ under real Telegram load
- **RISK-007**: Optional local embeddings can pull large model dependencies and need explicit deployment sizing
- **RISK-008**: Live Telegram behavior may surface API or permission details not covered by offline tests
- **RISK-009**: Cross-chat learning can leak context across communities if profile permissions are misconfigured or tested only on synthetic scopes
- **RISK-010**: Runtime prompt/provider changes can make answers non-reproducible unless selected templates, models and reasoning levels are logged with AI usage
- **RISK-011**: `/settings` preview/confirmation reduces broad-change surprises, but concurrent admins can still preview overlapping scoped changes and apply the latest active token
- **RISK-012**: GitHub Actions has been defined locally but the first hosted run still needs to pass after the branch is pushed
- **RISK-013**: Telegram Desktop JSON exports and Bot API update dumps can contain historical group content that predates bot consent; operators must review export files and target scopes before import
- **RISK-014**: Docker smoke proves local image startup, health checks and migrations, but production server sizing and secret injection still need deployment-specific validation

- **ASSUMPTION-001**: The project remains Python 3.12 and `uv` managed
- **ASSUMPTION-002**: v1 remains RAG memory, not online fine-tuning
- **ASSUMPTION-003**: Local development uses `AI_PROVIDER=fake`
- **ASSUMPTION-004**: Real deployments will use either OpenAI-compatible chat/embeddings or local embeddings through the provider interface
- **ASSUMPTION-005**: The default storage remains SQLite plus Markdown until the project outgrows a single-node deployment
- **ASSUMPTION-006**: Telegram-admin settings changes are limited to non-secret values unless a dedicated secret-management workflow is added

## 11. References

- Telegram Bot API: https://core.telegram.org/bots/api
- Telegram bot features and privacy mode: https://core.telegram.org/bots/features
- aiogram documentation: https://docs.aiogram.dev/
- OpenAI embeddings guide: https://platform.openai.com/docs/guides/embeddings
- `uv` documentation: https://docs.astral.sh/uv/
- `ruff` documentation: https://docs.astral.sh/ruff/
- `ty` documentation: https://docs.astral.sh/ty/
- Inspiration project: https://github.com/lyzcoote/go-shitfuckllm

## 12. Changelog

- **2026-06-16 / v0.1**: Initial implementation plan created
- **2026-06-16 / v0.2**: Plan converted into a lifecycle North Star after PoC scaffold implementation, dependency bumps, `ty` adoption and warning cleanup
- **2026-06-22 / v0.3**: Added various roadmap items
- **2026-06-23 / v0.4**: Implemented Phase 1 correctness work: idle summarization, concrete-scope global summaries, Alembic-backed migration, idempotent policies and duplicate ingestion, message-tier retrieval, import/export validation, `/help`, command registration and 21-test verification baseline
- **2026-06-23 / v0.5**: Implemented Phase 2 memory-quality work: canonical categorized compaction, deterministic deduplication, source links and scope metadata, per-scope memory settings, retention pruning, review-before-compact approval flow and 24-test verification baseline
- **2026-06-23 / v0.6**: Finished remaining Phase 2 TODOs: rich manual note metadata/edit/delete, named cross-chat profiles with explicit source-scope permissions, bounded message-range summaries, reusable summary templates and focused Phase 2 remaining tests
- **2026-06-23 / v0.7**: Implemented Phase 3 provider and AI hardening: OpenAI-compatible retries, embedding usage telemetry, `doctor`, stronger redaction, versioned prompts, insufficient-context answers, system prompt templates, one-shot AI overrides, provider profiles and scoped model/reasoning settings
- **2026-06-23 / v0.8**: Implemented Phase 4 Telegram integration coverage: dispatcher-fed updates, mocked Bot API calls, valid webhook and polling startup tests, full command matrix coverage, ingestion edge cases, optional live smoke test, Telegram send failure/rate-limit handling and export document chunking
- **2026-06-23 / v0.9**: Implemented non-Docker/non-CI Phase 5 operations, observability and security: structured logging, audit export, persisted forget confirmations, DB maintenance commands, privacy/secrets/operations/release docs, reloadless runtime settings and database-backed editable safe settings; Docker runtime verification and CI workflow are explicitly deferred until Docker is available locally
- **2026-06-23 / v0.10**: Implemented Phase 6 product completeness: scoped memory status and topic discovery, skipped-message diagnostics, Telegram Desktop JSON import, memory item browse/delete/refresh, supported non-text metadata ingestion, multilingual prompt guidance, compact answer source references and confirmed /settings changes; Docker and CI work remained deferred as previously recorded.
- **2026-06-24 / v0.11**: Closed deferred Docker and CI work plus remaining offline gaps: package version `0.8.0`, multi-stage non-root Docker image, optional-env compose smoke, GitHub Actions gates, Alembic Docker root fix, broader CLI tests, `/settings` command tests, generic Telegram JSON update dump import, representative memory quality workflow and configurable/observable admin cache.
