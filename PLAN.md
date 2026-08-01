---
goal: Telegram Group Memory Assistant PoC v1
version: 0.43
date_created: 2026-06-16
last_updated: 2026-08-01
owner: PZP-AI
status: In progress — Plan 021 Phase 6 is implemented and locally validated; Phase 7 is next while live split-deployment/Telegram acceptance and elapsed-retention validation remain deferred
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
- **REQ-008**: Store raw messages and durable knowledge authoritatively in SQLite, with deterministic rebuildable Markdown projections
- **REQ-009**: Support provider-agnostic AI through explicit chat and embedding protocols
- **REQ-010**: Provide deterministic offline behavior through a fake provider
- **REQ-011**: Provide import/export workflows so development does not depend on Telegram historical backfill
- **REQ-012**: Document setup, privacy constraints, commands, deployment and testing
- **REQ-013**: Support controlled cross-chat retrieval across explicitly selected channels, groups and forum topics without copying content into the destination scope
- **REQ-014**: Allow admins to summarize a bounded message range between two Telegram messages, with optional reusable summary templates
- **REQ-015**: Allow admins to customize prompt templates, select configured AI providers, switch models and tune reasoning level without code changes
- **REQ-016**: Support reloadless runtime configuration and Telegram-native settings management for admins
- **REQ-017**: Support multiple reloadless API credentials inside one provider profile, with sticky healthy selection, classified bounded failover and sanitized bot-admin notification
- **REQ-018**: Provide one responsive Nuxt control plane that works both as a Telegram Mini App and as a normal browser application authenticated through current Telegram mechanisms
- **REQ-019**: Let bot admins manage bot-wide settings, provider profiles/secrets, managed chats and bot admins, while Telegram-native or delegated chat admins remain restricted to the chats and topics they actually administer
- **REQ-020**: Let authorized users query a selected chat/topic with an explicit provider profile, supported model and reasoning effort, inspect bounded status/statistics/audit data, browse memory and stream scoped exports

- **CON-001**: Telegram Bot API does not provide arbitrary full historical group backfill. v1 learns live updates plus manual JSONL imports
- **CON-002**: Reading non-command group messages requires BotFather privacy mode disabled or the bot running as a group admin
- **CON-003**: Default learning must remain disabled unless explicitly enabled
- **CON-004**: Use `uv`, `ruff`, `ty` and `pytest`. Do not add `mypy`
- **CON-005**: Keep tests offline by default. Any live Telegram tests must be explicitly marked `telegram_live`
- **CON-006**: Keep one public control-plane origin through Caddy: Nuxt SSR owns presentation only, FastAPI owns `/api`, `/auth`, `/telegram/webhook`, `/health`, identity, authorization, secrets and business behavior, and both private services remain independently deployable without creating another SQLite writer

- **SEC-001**: Never commit `.env`, raw runtime data, SQLite DBs, or generated memory
- **SEC-002**: Reject Telegram webhook calls with a missing or wrong secret token
- **SEC-003**: Redact secret-like values before sending prompts to external providers
- **SEC-004**: Require two-step confirmation for destructive forget operations
- **SEC-005**: Add retention, audit and privacy controls before real group rollout
- **SEC-006**: Authorize review approval and rejection against each review's persisted owning scope before mutation
- **SEC-007**: Keep pending review content out of Markdown and reconcile every generated memory file after deletion, retention or forget operations
- **SEC-008**: Never promote raw chat to durable memory when summary generation fails; retain retryable source state and content-free diagnostics
- **SEC-009**: Authenticate normal browsers with Telegram OIDC Authorization Code plus PKCE and server-validated issuer, audience, signature, expiry and state; authenticate Mini Apps only from fresh server-validated `initData`, never `initDataUnsafe`
- **SEC-010**: Keep managed provider credentials envelope-encrypted at rest with deployment-supplied master keys outside the database; never place raw keys in Telegram, runtime settings, logs, audit payloads, telemetry, browser storage or backups without separate master-key protection
- **SEC-011**: Mask managed credentials by default and allow Option A full reveal only to a bot admin after fresh step-up authentication, through one audited non-cacheable response; environment-backed bootstrap secrets remain non-revealable

## 2. Current Architecture

### Runtime And Packaging

- **DONE**: `pyproject.toml` defines Python 3.12 package `pzp-ai`
- **DONE**: `uv.lock` exists and CI/Docker consume it immutably with uv 0.12.0 pinned in both build surfaces
- **DONE**: Runtime dependencies include `aiogram`, `fastapi`, `uvicorn[standard]`, `sqlalchemy[asyncio]`, `aiosqlite`, `alembic`, `openai`, `numpy`, `typer`, `rich`, `structlog`, `orjson`, `cryptography` and `PyJWT[crypto]`
- **DONE**: Dev dependencies include `pytest`, `pytest-asyncio`, `pytest-cov`, `respx`, `freezegun`, `httpx2`, `ruff` and `ty`
- **DONE**: Optional local embedding extra includes `sentence-transformers`
- **DONE**: `pzp-bot` console script points to `pzp_ai_bot.main:app`
- **DONE**: `.env.example`, `.gitignore`, focused Docker ignores, independent multi-stage non-root bot/frontend Dockerfiles, `Caddyfile`, three-service `docker-compose.yml` and `.github/workflows/ci.yml` exist
- **DONE**: CI checks lock consistency, performs a frozen development sync, upgrades one isolated runner-temp database and runs `alembic check` against the same schema so dependency or model/history drift fails before later gates
- **DONE**: CI measures statement and branch coverage, prints missing lines and fails below the conservative 78% floor; the 2026-07-29 starting baseline is 81%
- **DONE**: The 2026-07-30 Plan 020 checkpoint passed whole-project Ruff format/lint and `ty`, a fresh full suite with 228 passed and 1 skipped at 82.43% branch coverage, and empty-database upgrade plus metadata drift checks through revision 0012

### Source Layout

- **DONE**: `src/pzp_ai_bot/main.py` provides the Typer CLI
- **DONE**: `src/pzp_ai_bot/config.py` loads environment settings
- **DONE**: `src/pzp_ai_bot/app_context.py` composes settings, DB sessions, one reloadable provider registry, memory service, admin service and the optional control-plane identity/provider-vault runtime
- **DONE**: `src/pzp_ai_bot/control_plane/` owns Telegram Mini App/OIDC validation, encrypted one-time transactions, opaque sessions, actor/capability resolution, action-bound step-up, envelope-encrypted managed provider secrets, provider APIs, origin/CSRF checks and security headers without importing Nuxt
- **DONE**: `src/pzp_ai_bot/control_plane/development.py` provides the command-scoped loopback FastAPI composition boundary with backend-selected development identity, real sessions/CSRF/exact-action step-up, stable ignored local vault-key fallback and offline-by-default optional Telegram webhook connectivity
- **DONE**: `web/` builds a Nuxt SSR Node presentation service whose server render contains only public shell/neutral protected-route state; auth/session bootstrap and protected data remain client-only, and Nitro owns no Python business API, provider or database behavior
- **DONE**: `Caddyfile` is the production same-origin edge contract: reserved backend paths reach the private webhook bot, all other paths reach private Nuxt SSR, and cookie, authorization and CSRF headers are removed before frontend proxying
- **DONE**: `src/pzp_ai_bot/ai/` defines provider protocols and fake/OpenAI/local provider implementations
- **DONE**: `src/pzp_ai_bot/db/` defines async SQLAlchemy models and session helpers; cohesive implementations live in `db/repos/`, and `db/repositories.py` remains a stable compatibility re-export facade
- **DONE**: `src/pzp_ai_bot/memory/` defines scopes, policy evaluation, vectors and deterministic Markdown projections; `MemoryService` remains the stable `build_context` facade while contracts, transfer, provider operations/telemetry, settings/profiles/templates, retrieval, mutation/reviews/retention and summary/compaction orchestration live in cohesive collaborator modules
- **DONE**: `src/pzp_ai_bot/telegram/` defines admin checks, utility parsing, polling/webhook runtime and a stable root router that composes characterized command-family builders for learning, operations, settings, retrieval and memory behavior through shared authorization/safe-reply helpers
- **DONE**: `alembic/versions/0001_initial.py` creates the initial SQLite schema, revisions 0002 through 0006 add the Phase 2-6 data surfaces, `0007_align_telegram_identifier_types.py` aligns persisted Telegram identifiers with the models' `BigInteger` declarations, `0008_message_logical_uniqueness.py` reconciles duplicate logical messages and enforces their SQLite expression index, `0009_summary_claims.py` adds persisted bounded summary-work claims, `0010_remove_cross_scope_can_learn.py` removes the unused cross-scope learning-permission column without changing query permissions, `0011_canonical_scope_snapshots.py` adds validated per-scope canonical derived-cache rows, `0012_telemetry_scope_indexes.py` normalizes usage scope and indexes usage/event query paths, `0013_control_plane_identity_access.py` adds indexed control-plane identity/session/OIDC/admin/managed-chat state, and `0014_provider_credential_pools.py` adds versioned managed provider profiles, encrypted credentials and pool state
- **DONE**: `docs/` contains setup, architecture, command, privacy, deployment, operations, secrets, release and test documentation
- **DONE**: `tests/` contains offline unit/integration coverage for core behavior

## 3. Implemented Behavior

### CLI

- **DONE**: `pzp-bot migrate` applies Alembic migrations to `head` and records `alembic_version`
- **DONE**: `pzp-bot polling` builds context, deletes Telegram webhook and starts polling
- **DONE**: `pzp-bot webhook` builds context, sets Telegram webhook and starts FastAPI
- **DONE**: `pzp-bot summarize-now --scope <scope>` summarizes pending messages and compacts `MEMORY.md`
- **DONE**: `pzp-bot export-memory --format md|jsonl --scope <scope> [--out path]` keyset-pages knowledge and streams bounded chunks directly to a file or stdout; global Markdown streams the canonical file and unsupported formats or invalid scopes are rejected before opening the app context
- **DONE**: `pzp-bot import-jsonl --file path --chat-id id [--thread-id id]` incrementally validates up to 512 MiB/100,000 rows, embeds outside transactions and commits retry-safe content/source tuples in independently audited 100-row checkpoints with precise line errors
- **DONE**: `pzp-bot import-telegram-json --file path --chat-id id [--thread-id id]` uses pinned `ijson==3.5.1` iterators for supported Telegram Desktop and Bot API shapes, rejects unsupported shapes before business writes and imports retry-safe logical messages in independently audited 100-row checkpoints
- **DONE**: `pzp-bot show-config` prints non-secret runtime settings, provider profiles, default system prompt, reasoning level, safe control-plane configured-state/key-ID/TTL metadata and redacted credential-bearing database URLs
- **DONE**: `pzp-bot doctor` checks configured provider profiles, missing OpenAI-compatible API keys, missing local optional dependencies, SQLite parent write access and memory directory write access
- **DONE**: `pzp-bot audit-events --limit N [--event-type name] [--scope scope] [--format text|jsonl] [--out path]` reviews or exports recent `bot_events`
- **DONE**: `pzp-bot runtime-settings [--reload] [--set key=value]...` views, reloads or edits validated safe database-backed runtime settings
- **DONE**: `pzp-bot db-stats`, `db-integrity`, `db-vacuum` and `db-backup --out path` provide SQLite stats, integrity check, vacuum and online backup maintenance commands
- **DONE**: `pzp-bot secrets status` reports only safe vault counts/key IDs, and `pzp-bot secrets rewrap --to <master-key-id>` performs an acknowledged transaction-safe DEK rewrap with counts-only output

### Telegram Commands

- **DONE**: `/ask [profile=name] [provider=name] [system=name] [model=name] [reasoning=low|medium|high] [prompt="one-shot system prompt"] <question>` answers from current scope memory by default, can opt into explicitly permissioned cross-chat profiles, supports admin-only one-shot provider/model/reasoning/system-prompt overrides and includes compact source references when the provider output does not already cite them
- **DONE**: `/memory_search [profile=name] [provider=name] [embedding_model=name] <query>` returns formatted retrieval results with the same optional memory-profile expansion and admin-only query embedding provider/model overrides
- **DONE**: `/memory_export [md|jsonl] [scope=...]` incrementally sends UTF-8-safe deterministic document parts below the 45 MiB safety margin with scope/source metadata where available and rejects unknown tokens
- **DONE**: `/stats` returns global message, learned, skipped, knowledge, embedding, event and pending-review counts; `/stats scope=...` and `/memory_status` return richer per-scope message, memory and AI usage status
- **DONE**: `/learn_on`, `/learn_off`, `/learn_status` control/report current scope and show effective memory settings
- **DONE**: `/allow_chat`, `/deny_chat` enable/disable the chat-level scope
- **DONE**: `/allow_topic`, `/deny_topic` enable/disable the current topic scope
- **DONE**: `/allow_user <id|reply>` and `/deny_user <id|reply>` update author policy
- **DONE**: `/allow_admins on|off` stores admin-author learning policy through the shared strict boolean parser; missing or invalid values return usage without changing consent
- **DONE**: `/remember [tags=a,b] [priority=N] [source=url] <text>` creates a manual durable memory note with structured metadata
- **DONE**: `/summarize_now [template=name] [provider=name] [model=name] [reasoning=low|medium|high] [prompt="one-shot instruction"]` summarizes pending messages for the current scope with built-in/custom summary templates plus admin-only provider/model/reasoning/one-shot prompt overrides
- **DONE**: `/memory_settings` directly edits scoped top-k, summary batch size, summary/chat/embedding models, provider profile, reasoning level, system prompt template, retention and review-before-compact settings; `/settings` previews scoped changes and applies them only after confirmation
- **DONE**: `/memory_prune` applies configured retention pruning for a scope
- **DONE**: `/memory_reviews`, `/memory_approve` and `/memory_reject` support review-before-compact workflows; approval and rejection resolve and authorize the review's persisted owning scope before mutation
- **DONE**: `/memory_notes`, `/memory_note_edit` and `/memory_note_delete` list, edit and delete manual notes
- **DONE**: `/memory_profiles`, `/memory_profile_set` and `/memory_profile_allow` define named source-scope profiles and explicit destination permissions
- **DONE**: `/memory_templates` and `/memory_template_set` list built-in templates and manage custom summary templates
- **DONE**: `/system_prompts` and `/system_prompt_set` list built-in answer system prompts and manage custom system prompts
- **DONE**: `/summarize_range start=<message_id> end=<message_id> [template=name] [provider=name] [model=name] [prompt="one-shot instruction"]` summarizes a bounded learned message range and records source IDs, date interval, template and AI settings metadata
- **DONE**: `/forget scope=this_topic|this_chat|all confirm=<token>` uses persisted expiring two-step confirmation and deletes scoped memory
- **DONE**: `/audit_events [scope=this_topic|this_chat|all] [event_type=name] [limit=N]` exposes recent `bot_events` for admin review
- **DONE**: `/runtime_settings [reload=on] [key=value ...]` views, reloads or edits validated safe global runtime defaults from Telegram and is restricted to configured bot admins
- **DONE**: `/memory_scopes`, `/memory_debug`, `/memory_items`, `/memory_item_delete` and `/memory_item_refresh` provide topic/scope discovery, skipped-message diagnostics, memory browsing, item deletion and item refresh workflows from Telegram
- **DONE**: `/help` returns Telegram-native command discovery text

### Persistence And Memory

- **DONE**: SQLite tables exist for `messages`, `knowledge_items`, `embeddings`, `learning_policies`, `scope_metadata`, `scope_settings`, `memory_review_items`, `memory_profiles`, `memory_profile_scopes`, `memory_scope_permissions`, `summary_templates`, `system_prompt_templates`, `runtime_settings`, `forget_challenges`, `settings_challenges`, `canonical_scope_snapshots`, `bot_events`, `ai_usage`, `control_plane_users`, `control_plane_sessions`, `oidc_auth_transactions`, `bot_admin_grants`, `chat_admin_grants` and `managed_chats`
- **DONE**: Raw messages are stored with Telegram IDs, chat/thread/user IDs, optional chat/topic/source-link metadata, text, normalized text, learned flag, skip reason, reply ID and timestamps
- **DONE**: Knowledge items store kind, content, scope, source message IDs, JSON metadata and timestamps
- **DONE**: Embeddings store target type, target ID, scope, provider, model, dimensions, vector blob and timestamp
- **DONE**: Learning policies store global/chat/topic/user/admin toggles and are evaluated from one immutable transaction-local snapshot per decision or multi-scope report without cross-request caching
- **DONE**: AI usage records ask, summarize, range-summary, canonical-compaction and embedding provider/model/tokens/latency/error plus JSON metadata for provider profile, selected models, prompt/template versions, reasoning level and embedding count/dimensions; every chat/embedding call produces the same immutable safe telemetry record, and failures persist a bounded summary plus exception type in a fresh transaction only after business rollback or claim release
- **DONE**: New AI usage rows normalize chat/topic scope into dedicated columns, revision 0012 safely backfills recognized legacy metadata, and status performs two SQL aggregate queries with correct global/chat/topic hierarchy; composite usage scope/operation/error-time and event type/scope-time indexes are verified through SQLite query plans
- **DONE**: `data/memory/MEMORY.md` is canonically compacted from knowledge items into scoped Facts, Decisions, Preferences, Open Questions and Todos sections with deduplicated sources
- **DONE**: Canonical entries are persisted as validated per-exact-scope derived snapshots whose fingerprints cover item identity/content/source/update state plus every output-affecting provider and prompt setting; routine mutations refresh only dirty scopes, no-op compaction reuses unchanged entries, concurrent drift discards stale results, failures retain the last valid snapshot for retry, corrupt rows recompute, deleted scopes are removed and `compact_memory(force=True)` provides a full repair path
- **DONE**: `data/memory/daily/YYYY-MM-DD/<chat_id>_<thread_id>.md` files are deterministically rebuilt from committed knowledge items after durable mutations and on startup, with obsolete generated files removed through containment-checked paths
- **DONE**: `FakeProvider` gives deterministic chat, canonical compaction and embedding behavior
- **DONE**: `OpenAICompatibleProvider` supports configurable chat and embedding calls, including environment/runtime/scope/one-shot model selection and outbound `reasoning_effort` for recognized OpenAI reasoning-model IDs
- **DONE**: `LocalSentenceTransformerEmbeddingProvider` supports optional local embeddings when the `local` extra is installed
- **DONE**: Retrieval normalizes each query once, batch-hydrates targets in four bounded SQL statements per source scope, admits durable knowledge before recent learned raw messages and defaults to a configurable 5,000-candidate cap
- **DONE**: Cross-chat retrieval is disabled by default and only expands through named profiles whose source scopes have destination permission and enabled learning policy; profile source and permission management is restricted to configured bot admins
- **DONE**: Cross-scope profile permissions expose only the implemented retrieval contract: destination `can_query` plus each source scope's own learning policy; obsolete `learn=` and all unknown `/memory_profile_allow` keys fail before mutation
- **DONE**: `/ask` falls back to deterministic scoped results if chat generation fails
- **DONE**: Duplicate Telegram message ingestion is database-idempotent under concurrent main-chat redelivery through an atomic insert and unique logical-scope expression index; sequential duplicates still no-op and do not duplicate message embeddings
- **DONE**: Global summarization groups pending messages by concrete chat/topic scope instead of creating mixed global knowledge items
- **DONE**: Pending summaries are atomically claimed in oldest-first batches capped by the effective `summary_batch_size`, provider work runs without a live database session, compare-and-set finalization creates exactly one knowledge/review result per claim, provider failures release claims and abandoned claims become recoverable after 900 seconds without promoting raw chat
- **DONE**: Manual summarization drains all eligible bounded batches, ingestion processes at most one batch per update and each idle tick processes at most one batch per eligible concrete scope
- **DONE**: Per-scope memory settings override top-k, summary batch size, summary/chat/embedding models, provider profile, reasoning level, system prompt template, raw-message retention, knowledge retention and review-before-compact defaults
- **DONE**: Retention pruning can remove old raw messages and message embeddings without deleting durable summaries
- **DONE**: Optional review-before-compact mode keeps proposed summaries in SQLite only and requires approval before durable knowledge insertion or Markdown projection
- **DONE**: Summary provider exceptions and empty responses create no durable content, keep source messages pending, return zero completed messages and persist only scoped content-free failure diagnostics before a later retry
- **DONE**: Manual notes can be tagged, prioritized, sourced, listed, edited, deleted and exported with metadata
- **DONE**: Bounded range summaries use built-in, custom or one-shot templates and store boundary message IDs, source IDs, date intervals and AI settings metadata
- **DONE**: OpenAI-compatible chat and embedding calls use bounded retry/backoff and configurable redaction for secret-like key/value pairs, quoted JSON secret fields, bearer/common token forms, emails, phone numbers and custom regex patterns
- **DONE**: Provider profiles can be configured through `AI_PROVIDER_PROFILES`; missing OpenAI-compatible credentials fail closed and local embedding missing extras raise actionable install guidance
- **DONE**: `/ask` returns an explicit insufficient-context answer when scoped memory is empty or below the configured score threshold

### Telegram Runtime

- **DONE**: Polling runtime calls `delete_webhook` before `start_polling`
- **DONE**: Webhook runtime sets the webhook on lifespan startup
- **DONE**: Webhook endpoint validates `X-Telegram-Bot-Api-Secret-Token` and webhook startup rejects blank or placeholder `WEBHOOK_SECRET` values such as `change-me`, `changeme`, `placeholder`, `default` and `secret`
- **DONE**: Topic scope extraction uses `is_topic_message` and `message_thread_id`
- **DONE**: Admin service caches `get_chat_administrators` results, honors configured `BOT_ADMINS`, supports configurable `ADMIN_CACHE_TTL_SECONDS` and exposes cache stats for diagnostics
- **DONE**: Polling and webhook runtimes supervise a background idle summarizer using `SUMMARY_IDLE_SECONDS`; ordinary iteration failures retry with bounded backoff while cancellation remains prompt
- **DONE**: Polling and webhook startup register the current Telegram command list through `set_my_commands`
- **DONE**: Polling and webhook runtimes supervise a background runtime-settings refresher using `RUNTIME_SETTINGS_REFRESH_SECONDS`, with typed process-local health state and content-free structured failure diagnostics
- **DONE**: Webhook `GET /health` returns HTTP 200 only while both critical task handles are running and not done, and HTTP 503 with per-task booleans when either task is dead
- **DONE**: Telegram update handling uses structured `structlog` logging with safe chat/thread/user/message metadata, duration and error fields
- **DONE**: Telegram commands that accept `scope=` authorize the target scope, so chat admins can operate only on the current chat while configured bot admins can operate across chats and `scope=all`

### Control-Plane Identity

- **DONE**: Enabled control-plane configuration requires one exact path-free HTTPS public origin, the Telegram bot token and OIDC client credentials; the callback is fixed, TTL/rate limits and every configured base64-encoded 32-byte master key are validated, the active key is required and `show-config` never emits OIDC or master-key material
- **DONE**: Telegram Mini App `initData` is strictly parsed with duplicate rejection, documented HMAC validation, constant-time comparison, future/age bounds and typed human Telegram identity extraction; `initDataUnsafe` is never trusted
- **DONE**: Telegram browser login uses discovery plus Authorization Code, S256 PKCE, state and nonce; bounded httpx token/JWKS calls and PyJWT verification stay fixed to RS256 and require the expected issuer, audience, numeric Telegram ID and time claims
- **DONE**: One-time OIDC rows store only a state hash and AES-GCM-encrypted PKCE verifier bound to the state/fixed callback, consume atomically before callback errors are handled and reject replay, expiry, unsafe return paths, missing codes, OAuth errors and identity mismatch
- **DONE**: Opaque server sessions persist only SHA-256 cookie/CSRF hashes with idle/absolute expiry, rotation, revocation, account-switch replacement, exact step-up action/time and cleanup; browser and Mini App entry paths upsert one shared Telegram user model
- **DONE**: Shared authorization combines configured bot admins, active managed bot-admin grants, active delegated chat grants and concrete Telegram-native admin lookup without importing Nuxt; grants and managed-chat state are resolved afresh so removal/pause takes effect immediately
- **DONE**: FastAPI provides browser start/callback, Mini App exchange, session, logout, browser OIDC step-up and Mini App step-up routes with separate rate buckets, Secure/HttpOnly/SameSite cookies, strict same-origin and CSRF enforcement, no-store/security headers, Telegram-limited CSP and content-free auth audit reasons
- **DONE**: Nuxt keeps session and CSRF state in process memory only, performs exactly one signed Mini App exchange or regular-browser session restoration, redirects browser login through the backend OIDC route and uses capabilities only for navigation UX rather than authority
- **DONE**: `web/pnpm dev` coordinates the uv-run development backend and Nuxt under one loopback origin, preserves reserved proxy prefixes, avoids Nuxt internal API collisions and owns complete child trees through persistent PowerShell 7 wrappers on Windows; development routes remain absent from production applications and artifacts
- **DONE**: Phase 3 authentication and Phase 4 provider-vault endpoints are attached to enabled webhook FastAPI applications; the production presentation boundary is independently served by Nuxt SSR behind same-origin Caddy rather than by FastAPI
- **PARTIAL**: The identity shell and provider-pool console are deployable in webhook mode, but the remaining bot settings, managed-chat, query, memory, stats, audit and broader operations product APIs/pages belong to later Plan 021 phases; polling remains a separate bot-only local/manual mode rather than a second production web writer

### Provider Vault And Managed Profiles

- **DONE**: The provider vault derives domain-separated wrapping/fingerprint keys through HKDF-SHA-256, encrypts each secret and its random DEK with AES-256-GCM plus credential-bound authenticated data, stores only ciphertext/safe display metadata and reports generic tamper/decrypt errors
- **DONE**: Managed profiles and credentials persist behind revision 0014 repositories with explicit optimistic versions, stable ordering, safe limits, tombstone deletion that clears encrypted fields and one transaction-scoped persisted pool-state row
- **DONE**: Environment provider profiles remain immutable bootstrap/recovery sources; same-name managed profiles override their non-secret configuration and synchronize the environment credential as immutable non-revealable pool metadata without restarting the process
- **DONE**: Bot-admin-only typed APIs create/list/update/delete managed profiles and credentials, invalidate provider instances immediately, audit only safe metadata, return the current safe version on conflicts and require exact-action fresh step-up for credential creation, deletion and reveal
- **DONE**: Full database-managed credential reveal is isolated to audited `POST`, returns private no-store/no-cache headers and is held by Nuxt only in component memory with automatic 30-second and lifecycle clearing plus a separate explicit Copy action
- **DONE**: Vault status and rewrap CLI operations reveal only counts/key IDs; rewrap decrypt-verifies every new envelope before any write so an interrupted preparation leaves the entire credential set on its original master keys
- **DONE**: Managed OpenAI-compatible chat and embedding use typed opaque leases, classified bounded same-key retry, stable one-pass circular rotation, persisted sticky active selection, cooldown/hard-disable/recovery transitions, per-profile locks plus SQLite versions, revision-aware cache invalidation and safe attempt telemetry
- **DONE**: Revision 0015 persists deduplicated safe alert intents/deliveries; a supervised non-critical worker resolves current effective bot admins, handles retry-after/unreachable recipients and cannot affect provider results or critical health
- **DONE**: Bot-admin pool/credential-test/alert APIs and the SSR-safe Nuxt Providers page expose masked health/history, independently rate-limited constant-input checks, controlled reveal, sanitized alert retry and explicit provider-cost/local-download warnings

### Documentation

- **DONE**: `README.md` contains a quick-start runbook
- **DONE**: `docs/setup.md` documents local setup and Telegram preparation
- **DONE**: `docs/architecture.md` documents package boundaries and memory files
- **DONE**: `docs/commands.md` documents every registered Telegram and Typer CLI command, including operational cautions and links to canonical setup, deployment, privacy and operations guidance
- **DONE**: `docs/privacy.md` documents privacy mode, storage, retention, review workflow and provider notes
- **DONE**: `docs/deployment.md` documents bot-only polling, direct webhook operation and the three-service Caddy/Nuxt SSR/FastAPI production topology with independent image replacement
- **DONE**: `docs/operations.md` documents structured logs, audit review, Markdown projection recovery, runtime settings and DB maintenance
- **DONE**: `docs/secrets.md` documents `.env`, bootstrap and managed provider keys, the envelope-encryption boundary, controlled reveal/rewrap, Telegram token/webhook secret rotation and container secret guidance
- **DONE**: `docs/release.md` documents versioning, release checks, migration state and manual Telegram smoke expectations
- **DONE**: `docs/testing.md` documents offline verification commands
- **DONE**: Setup, architecture, deployment, secrets and testing docs distinguish frontend-only, combined offline, deliberate connected-Telegram and production modes, including the loopback mock-identity and colocated development-key boundaries

### Current Verification Baseline

- **DONE**: `uv sync --extra dev --extra local` passed on 2026-06-24 after plan v0.11
- **DONE**: `uv run ruff format --check .` passed on 2026-06-24 after plan v0.11
- **DONE**: `uv run ruff check .` passed on 2026-06-24 after plan v0.11
- **DONE**: `uv run ty check` passed on 2026-06-24 after plan v0.11
- **DONE**: `uv run pytest` passed on 2026-06-24 with 118 passed, 1 skipped and 4 existing aiosqlite thread warnings after plan v0.11
- **DONE**: Security hardening focused validation passed on 2026-06-24 with `uv run pytest tests/test_telegram_integration.py tests/test_webhook.py tests/test_ai_providers.py tests/test_cli.py -q` reporting 97 passed after independent verifier follow-up
- **DONE**: Post-security-hardening verification passed on 2026-06-24 with `uv run ruff format --check .`, `uv run ruff check .`, `uv run ty check` and `uv run pytest -q` reporting 138 passed and 1 skipped after independent verifier follow-up
- **DONE**: SQLite-authoritative Markdown lifecycle verification passed on 2026-07-29 with the focused privacy/operations suite, `uv run ruff format --check .`, `uv run ruff check .`, `uv run ty check` and the full offline pytest suite
- **DONE**: Retryable summary-failure verification passed on 2026-07-29 with exception and empty-response coverage across review modes, the complete provider/memory-quality target, `uv run ruff format --check .`, `uv run ruff check .`, `uv run ty check` and the full offline pytest suite
- **DONE**: Background-task supervision verification passed on 2026-07-29 with recovery/backoff/cancellation/shutdown coverage, webhook HTTP 200/503 health and polling lifecycle tests, plus `uv run ruff format --check .`, `uv run ruff check .`, `uv run ty check` and the full offline pytest suite
- **DONE**: Strict `/allow_admins` consent validation passed on 2026-07-29 with missing/invalid non-mutation, explicit on/off and authorization-order coverage, the complete Telegram integration suite, `uv run ruff format --check .`, `uv run ruff check .`, `uv run ty check` and the full offline pytest suite
- **DONE**: Alembic model/history drift gating passed on 2026-07-29 after revision `0007_align_telegram_identifier_types` corrected nine historical Telegram identifier type mismatches; focused artifact/migration tests, `uv run alembic upgrade head`, `uv run alembic check`, Ruff format/lint, `ty` and the full offline pytest suite all passed against disposable state
- **DONE**: Bounded retrieval verification passed on 2026-07-29 with a deterministic 1,000/10,000-candidate benchmark reducing SQL statements from 1,001/10,001 to four at both sizes, representative durable-recall coverage, a 24-test focused target, Ruff format/lint, `ty` and the full offline pytest suite reporting 177 passed and 1 skipped
- **DONE**: Immutable lock consumption verification passed on 2026-07-29 with uv 0.12.0, `uv lock --check`, `uv sync --frozen --extra dev`, identical pre/post `uv.lock` SHA-256, six focused artifact tests, Ruff format/lint, `ty` and the full offline pytest suite reporting 179 passed, 1 skipped and 2 existing aiosqlite thread-shutdown warnings
- **DONE**: Complete CLI-reference verification passed on 2026-07-29 with a live Typer inventory regression, all six previously omitted commands documented, `uv run pzp-bot --help`, 14 focused CLI/artifact tests, Ruff format/lint, `ty` and the full offline pytest suite reporting 180 passed and 1 skipped
- **DONE**: Coverage regression verification passed on 2026-07-29 with a measured 80.72%/reported 81% statement-and-branch baseline, the configured 78% floor, visible missing lines, seven focused artifact tests, Ruff format/lint and `ty`; the complete coverage suite reported 180 passed and 1 skipped, while a temporary 82% override correctly exited non-zero without repository changes
- **DONE**: Policy-snapshot verification passed on 2026-07-29 with exact precedence/reason coverage and query-count regressions reducing one enabled decision from four policy SELECTs and six policy-only scopes from thirteen to one; 24 focused tests, Ruff format/lint and `ty` passed, and the complete branch-coverage suite reported 184 passed, 1 skipped and 81.09%
- **DONE**: Concurrent message-idempotency verification passed on 2026-07-29 with five repeated two-session main-chat races, distinct chat/topic coverage, upgrade-time duplicate/embedding reconciliation, direct expression-index inspection, 20 focused tests, disposable upgrade through 0008, clean Alembic check, Ruff format/lint and `ty`; the complete coverage suite reported 187 passed, 1 skipped and 81.08%
- **DONE**: Bounded summary-claim verification passed on 2026-07-29 with disjoint concurrent claims, idempotent release/finalize, stale recovery, retryable provider failures, direct/review results, blocked-provider write access, ingest/idle per-tick caps and full-backlog draining; fresh upgrade through 0009, downgrade/re-upgrade and clean Alembic checks passed, Ruff format/lint and `ty` passed, and the complete branch-coverage suite reported 196 passed, 1 skipped and 81.25%
- **DONE**: Retrieval-only profile permission verification passed on 2026-07-29 with unsupported command-key non-mutation, preserved query/source-policy behavior and revision 0010 upgrade coverage proving seeded true/false `can_query` values survive removal of unused `can_learn`; 104 focused tests, fresh plus downgrade/re-upgrade Alembic checks, Ruff format/lint and `ty` passed, and the complete branch-coverage suite reported 199 passed, 1 skipped and 81.27%
- **DONE**: `uv run alembic upgrade head` passed on 2026-06-24 through `0006_phase6_product_completeness` after plan v0.11
- **DONE**: `uv run pzp-bot --help` displayed all CLI commands on 2026-06-24 after plan v0.11
- **DONE**: `uv run pzp-bot doctor` passed on 2026-06-24 with the default fake provider after plan v0.11
- **DONE**: Current Docker runtime verification passed on 2026-07-30 after correcting the frozen production sync to install the project non-editably; image build, image `doctor`, empty-schema migration, Compose `doctor` and a Docker health transition to `healthy` all passed
- **DONE**: CI workflow validation passed locally through static artifact tests; `.github/workflows/ci.yml` runs format, lint, type check, tests, isolated-database Alembic upgrade and model/history drift detection, CLI help, doctor and Docker smoke on GitHub Actions
- **DONE**: Full local and Docker verification baseline was re-run on 2026-06-24 after plan v0.11
- **DONE**: The full post-acceptance baseline was rerun on 2026-07-30: Ruff format/lint, `ty`, focused provider tests and 229 passed with 1 skipped in the fresh complete branch-coverage suite; the owner also confirmed both hosted CI jobs green
- **DONE**: The Plan 021 Phase 4 checkpoint passed on 2026-08-01: immutable uv lock, Ruff format/lint, `ty`, 269 passed/1 skipped/4 expected failures at 82.83% branch coverage, disposable Alembic upgrade/check through revision 0014, vault CLI help, Nuxt format/lint/typecheck, 11 unit/component tests, OpenAPI drift, six generated routes, 22 generated text assets scanned clean and 6 desktop/mobile Playwright checks
- **DONE**: The approved pre-Phase-5 Nuxt SSR/Caddy transition checkpoint passed on 2026-08-01: Oxfmt, type-aware Oxlint/tsgolint, raw Nuxt typecheck, 11 unit/component tests, OpenAPI drift, a 2.75 MB production Node-server build, a 17-text-asset secret scan, nonce-aware SSR smoke and 10 desktop/mobile Playwright checks; whole-project Ruff format/lint, `ty` and the fresh offline suite passed with 272 tests, 1 skipped live Telegram test and 4 strict expected failures
- **DONE**: The Plan 021 Phase 5 checkpoint passed on 2026-08-01: immutable uv lock, Ruff format/lint, `ty`, focused provider/alert/control-plane verification with 64 passed and 4 expected failures, a fresh external-basetemp complete offline suite with 303 passed/1 skipped/4 expected failures, empty-database upgrade/check through revision 0015, CLI help and fake-provider doctor. Frontend gates passed Oxfmt, type-aware Oxlint/tsgolint, raw Nuxt typecheck, 12 unit/component tests, OpenAPI drift, a 2.8 MB production SSR build, 20 generated text assets scanned clean, direct provider-route SSR smoke and 14 desktop/mobile Playwright checks. Final audit regressions made environment credential metadata/deletion immutable and clear ineligible active-key pointers; 36 focused tests and a fresh post-fix full suite passed with the same 303/1/4 outcome and no warnings. Ten known non-fatal aiosqlite teardown warnings from the preceding full checkpoint remain a recurrence watch item.
- **DONE**: The combined control-plane development checkpoint passed on 2026-08-01: the fresh full Python suite reported 343 passed/1 skipped/4 strict xfails, Ruff format/lint and `ty` passed, and frontend gates passed Oxfmt, type-aware Oxlint/tsgolint, raw Nuxt typecheck, 27 unit/component tests, OpenAPI drift, a 2.8 MB production build, a complete 227-text-asset scan, direct SSR smoke and 16 desktop/mobile Playwright checks. A real disposable loopback Chromium run proved automatic backend-selected bot-admin login, protected API loading, exact-action step-up, prefix-correct proxying, Nuxt internal API isolation, stable Windows process ownership and complete two-port cleanup without enabling Telegram or OpenAI calls.
- **DONE**: The Plan 021 Phase 6 checkpoint passed on 2026-08-01: immutable uv lock, Ruff format/lint, `ty`, CLI help, fake-provider doctor, a fresh external-basetemp full suite with 355 passed/1 skipped/4 strict xfails and no warnings, disposable upgrade/check through revision 0016 and explicit legacy `removed` to `detached` migration coverage. Frontend gates passed Oxfmt, type-aware Oxlint/tsgolint, raw Nuxt typecheck, 30 unit/component tests, OpenAPI drift, a 2.89 MB production SSR build, a complete 236-text-asset scan, direct SSR smoke and all 22 desktop/mobile Playwright checks for Providers/Bot Settings/Chats hydration, prepared/DM enrollment presentation and pre-auth SSR data isolation. Live Telegram prepared-button selection, Docker/Caddy/TLS and the other release-environment gates remain pending.

## 4. Known Partial Implementations

- **PARTIAL**: Disposable-group Telegram command, privacy/admin, learning, summarization, retrieval, settings, export and forget behavior passed live on 2026-07-30; only retention after real elapsed age remains a manual Gate C validation.
- **PARTIAL**: The former single bot-image Docker topology passed local acceptance on 2026-07-30, but it has been superseded by the unexecuted Caddy/Nuxt SSR/webhook Compose topology; current live Docker build, routing, health, TLS, migration and independent-replacement acceptance remain deferred until the daemon is available.
- **PARTIAL**: Plan 021 Phases 1 through 6, the approved pre-Phase-5 SSR/Caddy transition and the combined loopback development workflow are implemented and locally validated: the revocable browser/Mini App identity boundary, encrypted multi-key provider pools, safe alert outbox, managed bot/delegated/native authority, requester-bound group enrollment, active/paused/detached memory lifecycle, safe runtime/scoped settings and Providers/Bot Settings/Chats SSR pages now exist through revision 0016. Query, memory, statistics, audit and broader operations workflows plus live split-deployment/BotFather/Telegram acceptance have not landed; Phase 7 is next.
- **DONE**: Managed OpenAI-compatible profiles support up to ten environment/database credentials with sticky healthy selection, bounded same-key retry and one-pass rotation, classified cooldown/hard-disable/recovery, restart/cross-instance-safe persisted state, revision-aware cache invalidation, safe telemetry, controlled exhaustion and deduplicated best-effort current-admin DMs.
- **PARTIAL**: The Nuxt SSR control-plane identity shell, safe hydration gate, FastAPI authentication/session/provider/bot-settings/managed-chat routes, Providers/Bot Settings/Chats pages, reusable controlled-reveal component and Caddy deployment artifacts exist, but the web query workspace, memory explorer, statistics, audit and broader operational dashboard remain unimplemented.

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

| Task     | Status | Description                                                                                                                          | Acceptance Criteria                                                                                                                                                                                               |
| -------- | ------ | ------------------------------------------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| TASK-011 | DONE   | Implement LLM-assisted canonical compaction from daily summaries into stable facts, decisions, preferences, open questions and todos | `MEMORY.md` is grouped by scope and semantic category; sources are preserved; fake provider test is deterministic                                                                                                 |
| TASK-012 | DONE   | Deduplicate semantically similar knowledge items during compaction                                                                   | Repeated facts do not produce repeated bullets; source IDs are merged                                                                                                                                             |
| TASK-013 | DONE   | Add source formatting with Telegram message references where possible                                                                | Memory entries include source IDs and, when chat username/link data exists, clickable Telegram links                                                                                                              |
| TASK-014 | DONE   | Store optional chat title, topic title, username and message permalink metadata                                                      | Exports are readable without raw numeric IDs only; migration and tests cover metadata                                                                                                                             |
| TASK-015 | DONE   | Add per-scope memory settings such as top-k, summary batch size and summary model                                                    | Scope-specific settings override defaults and are visible in `/learn_status`                                                                                                                                      |
| TASK-016 | DONE   | Add retention controls for raw messages and generated knowledge                                                                      | Admins can configure retention; old raw messages can be pruned without deleting durable summaries                                                                                                                 |
| TASK-017 | DONE   | Add memory review workflow before compacting sensitive scopes                                                                        | Optional mode writes proposed summaries and requires admin approval before knowledge insertion                                                                                                                    |
| TASK-018 | DONE   | Add richer manual note commands: tags, source, priority and edit/delete note                                                         | `/remember` notes can be listed, edited, deleted and exported with tags                                                                                                                                           |
| TASK-019 | DONE   | Add explicit cross-chat retrieval profiles for selected source channels, groups and topics                                           | Admin can define a named retrieval profile such as one channel, two groups and three topics; ingestion stays in each original source scope and retrieval can opt into the profile without leaking unrelated chats |
| TASK-020 | DONE   | Add destination query permissions for cross-chat memory retrieval                                                                    | A destination can query another scope only when its admin-managed query permission and the source scope's own learning policy both allow it; tests cover denied and allowed cross-scope access                    |
| TASK-021 | DONE   | Add message-range summarization between two Telegram messages                                                                        | Admin can reply/select/provide two message IDs; the bot checks both message dates and source scope, summarizes only the bounded range and records the date interval and source IDs                                |
| TASK-022 | DONE   | Add reusable summary templates for range and scope summaries                                                                         | Admin can choose configured templates for decisions, todos, meeting notes, incident recap, or custom formats; fake provider tests snapshot the prompt inputs                                                      |

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

| Task     | Status | Description                                                  | Acceptance Criteria                                                                                                                                                  |
| -------- | ------ | ------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| TASK-044 | DONE   | Implement structured logging with `structlog`                | Logs include event type, chat_id, thread_id, user_id when safe, duration and error                                                                                   |
| TASK-045 | DONE   | Add audit views or export for `bot_events`                   | Admin actions and destructive operations can be reviewed                                                                                                             |
| TASK-046 | DONE   | Persist forget confirmation challenges with expiry           | Restart does not create ambiguous destructive state; expired tokens cannot be reused                                                                                 |
| TASK-047 | DONE   | Add DB maintenance commands                                  | CLI supports vacuum, backup, stats and integrity check for SQLite                                                                                                    |
| TASK-048 | DONE   | Add privacy/data-processing documentation for real group use | Docs explain what is stored, sent to providers, retained, exported and deleted                                                                                       |
| TASK-049 | DONE   | Add retention pruning command and policy                     | Admin can prune raw messages older than configured window per scope                                                                                                  |
| TASK-050 | DONE   | Add secrets handling guidance for deployment                 | Docs cover `.env`, Docker secrets, provider keys and Telegram token rotation                                                                                         |
| TASK-051 | DONE   | Add Docker runtime verification                              | Docker build, container `doctor`, container migration and compose `doctor` smoke pass locally without starting Telegram polling                                      |
| TASK-052 | DONE   | Add CI workflow                                              | GitHub Actions runs format, lint, type check, tests, Alembic upgrade, CLI help, doctor and Docker smoke on push and pull request                                     |
| TASK-053 | DONE   | Add release checklist and versioning process                 | Releases specify migration state, config changes and manual Telegram smoke tests                                                                                     |
| TASK-054 | DONE   | Add reloadless runtime settings refresh                      | File/env-backed settings have a validated reload path; long-running polling and webhook runtimes apply non-secret setting changes without process restart where safe |
| TASK-055 | DONE   | Store editable bot settings in the database                  | Runtime settings that are safe to edit are persisted with schema, validation, defaults, scope overrides, audit events and rollback behavior                          |

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

### Phase 7 - Secure Operator Control Plane And Provider Resilience

Goal: add the requested operator experience and remove each provider profile's single-key failure domain without weakening Telegram scope authorization or secret handling.

Plan 021 execution checkpoint: Implementation Phases 1 through 6 (`TASK-001` through `TASK-051`), the separately approved Nuxt SSR/Caddy transition and the combined loopback development workflow are DONE and locally validated through 2026-08-01; Phase 7 is ready and not started. The frontend baseline remains pnpm-managed Node.js `24.18.0`, pnpm `11.18.0`, Nuxt `4.5.1`, Nuxt UI `4.10.0`, TypeScript `6.0.3`, oxlint `1.76.0`, `oxlint-tsgolint` `7.0.2001` and oxfmt `0.61.0`; Alembic is at `0016_managed_chat_enrollment_requests`, and `cryptography==49.0.0` protects both Phase 3 PKCE transactions and the provider vault while `PyJWT[crypto]==2.13.0` remains the RS256 ID-token verification stack. Live split-Docker/Caddy, registered real Telegram OIDC/Mini App prepared-button enrollment, authorized provider failover/admin-DM and backup/restore acceptance remain deployment/release gates rather than missing Phase 6 implementation.

| Task     | Status | Description                                                                                     | Acceptance Criteria                                                                                                                                                                                     |
| -------- | ------ | ----------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| TASK-068 | DONE   | Add the envelope-encrypted managed provider-secret vault and multi-key profile persistence     | Encrypted multi-key persistence, reloadless CRUD, reveal, master-key rewrap, credential health/test and revision-safe pool execution are implemented and locally verified                                |
| TASK-069 | DONE   | Add sticky provider-key selection, failure classification, bounded retry/failover and DM alerts | SDK classifier, restart/cross-instance concurrency, bounded exhaustion, safe telemetry and deduplicated non-fatal current-admin delivery regressions pass                                                |
| TASK-070 | DONE   | Add Telegram OIDC, Mini App `initData` validation, opaque sessions, CSRF and step-up            | Both entry modes resolve the same Telegram identity and revocable server-side session; stale/replayed/tampered auth and same-identity step-up checks fail closed                                          |
| TASK-071 | PARTIAL | Add the Nuxt 4/Vue 3/Nuxt UI SSR control plane and same-origin FastAPI API                    | The exact-pinned accessible SSR identity shell, safe hydration boundary, authenticated navigation, Providers/Bot Settings/Chats consoles, Phase 3-6 handlers and Caddy ingress artifacts are DONE; query/memory/stats/audit/operations handlers and pages remain TODO |
| TASK-072 | PARTIAL | Add bot/scoped settings, admin, managed-chat, query, memory, stats, health and audit workflows | Bot/scoped settings, owner/delegated-admin management and managed-chat enrollment/lifecycle are DONE with validated/audited scoped controls; query, memory, stats, health and audit workflows plus bounded streaming lists/exports remain TODO                         |
| TASK-073 | PARTIAL | Integrate runtime supervision, migrations, Docker/CI/docs and complete offline/live acceptance | SSR build/smoke, backend/frontend artifact gates, revision 0016 and docs are DONE; live Caddy/Compose/TLS/independent-replacement, real Telegram OIDC/Mini App, authorized provider failover/DM and backup/restore evidence remain |

## 6. Test Plan

### Tests Implemented

- **DONE**: `tests/test_scope.py` verifies topic scope extraction and scope parsing
- **DONE**: `tests/test_policy_and_ingestion.py` verifies learning-disabled skip, learning-enabled summarization, exact global/chat/topic/user/admin precedence and reason strings, one policy SELECT per decision/status, one policy SELECT across multi-scope reporting and mutation visibility on the next operation
- **DONE**: `tests/test_memory_service.py` verifies scoped remember/search/ask, forget confirmation and JSONL import/export
- **DONE**: `tests/test_webhook.py` verifies bad webhook secret rejection, fail-closed rejection of blank/default/placeholder webhook secrets, and critical-task health responses for HTTP 200 and 503
- **DONE**: `tests/test_telegram_integration.py` verifies dispatcher command behavior including strict non-mutating `/allow_admins` validation, target-scope authorization and review-decision ownership
- **DONE**: `tests/test_memory_correctness.py` verifies concrete-scope global/idle summarization, duplicate ingestion idempotency, nullable policy cleanup, message-embedding retrieval, JSONL validation, bounded constant-query retrieval, durable-first recall and vector numeric compatibility at 1,000 and 10,000 candidates
- **DONE**: `tests/test_memory_quality.py` verifies canonical compaction grouping/dedup/source links, per-scope settings, review approval, retention pruning without durable-summary deletion and representative memory-quality workflows
- **DONE**: `tests/test_memory_tools.py` verifies manual note metadata/edit/delete/export, cross-chat profile permission gating, range summaries with templates and fake-provider template prompt inputs
- **DONE**: `tests/test_ai_providers.py` verifies OpenAI-compatible retry/redaction payloads, local optional-dependency failure messaging, provider-profile fail-closed behavior, embedding usage metadata, insufficient-context answers, prompt/model/reasoning usage metadata and actual reasoning-effort forwarding
- **DONE**: `tests/test_telegram_integration.py` verifies aiogram dispatcher-fed command and normal-message updates, mocked Bot API methods, valid webhook dispatch, polling startup sequencing, command matrix coverage, ingestion edge cases, Telegram API failure/rate-limit handling, export document chunking, strict non-mutating `/allow_admins` validation, target-scope authorization and review-decision ownership
- **DONE**: `tests/test_telegram_live.py` provides optional skipped-by-default `telegram_live` smoke coverage for real Telegram credentials
- **DONE**: `tests/test_operations.py` verifies persisted forget confirmations across restarts, expired confirmation rejection, runtime settings refresh/audit events, audit scope filtering, SQLite stats and runtime-settings refresher scheduling
- **DONE**: `tests/test_product_controls.py` verifies scoped memory status and topic discovery, skipped-message diagnostics, Telegram Desktop JSON import, Bot API update dump import, representative import/export/forget memory quality, memory item browse/delete/refresh, `/settings` preview/confirmation and compact source references
- **DONE**: `tests/test_cli.py` verifies Alembic-backed migration through current head, persisted `BIGINT` Telegram identifier types, the authoritative message logical-scope index, upgrade-time duplicate/embedding reconciliation, query-permission preservation while removing unused `can_learn`, CLI validation errors, successful `summarize-now`, `export-memory`, `import-jsonl`, `show-config`, credential-redacted DB URL diagnostics, Phase 5 runtime settings/audit export commands and DB maintenance commands
- **DONE**: Plan 019 transfer regressions across `tests/test_memory_service.py`, `tests/test_product_controls.py`, `tests/test_cli.py` and `tests/test_telegram_integration.py` verify a 1,000-row/10-provider-call batch ratio, original JSONL line errors, per-batch partial failure and idempotent retry, file/row limits, ijson parsing without `Path.read_text`, unsupported-shape preflight, primary-key export order, streamed canonical Markdown, incremental CLI output and bounded UTF-8-safe Telegram document names/content
- **DONE**: `tests/test_telegram_commands.py` verifies command help text and mocked `set_my_commands` registration
- **DONE**: `tests/test_admin_service.py` verifies configurable admin-cache TTL, cache reuse and cache stats
- **DONE**: `tests/test_deployment_artifacts.py` verifies both Dockerfiles, focused Docker ignores, the private three-service Compose topology, exact Caddy pin/reserved paths/frontend credential-header removal, the shared pinned uv version, frozen non-editable bot installation, GitHub Actions quality gates including isolated Alembic upgrade/drift commands, Alembic runtime root discovery and complete CLI-reference coverage from the live Typer command inventory
- **DONE**: `tests/test_architecture_facades.py` characterizes all 52 public `MemoryService` methods, exact repository compatibility identities, exact Telegram router composition order and dependency direction without lower-layer Telegram or collaborator-to-facade back-imports
- **DONE**: `tests/conftest.py` builds isolated SQLite and fake-provider context
- **DONE**: `tests/test_control_plane_development.py` plus frontend session/reveal/coordinator/proxy/provider regressions verify command-scoped loopback settings, stable local key policy, production route isolation, real session/CSRF/exact-action development auth, offline/optional-connected lifecycles, Windows process-tree ownership, prefix-correct same-origin proxying and environment bootstrap-profile UX

### Tests Still Required

- **DONE**: TEST-004 successful CLI `summarize-now`, `export-memory`, `import-jsonl` and `show-config` coverage is implemented
- **DONE**: TEST-SEC-001 Telegram regressions verify non-admin export rejection, foreign-scope mutation rejection for chat admins, bot-admin foreign-scope allowance and bot-admin-only profile/runtime/template mutations
- **DONE**: TEST-SEC-002 provider and CLI regressions verify quoted JSON secret redaction and credential-bearing DB URL redaction
- **DONE**: TEST-013 Docker smoke test and documentation are implemented
- **DONE**: TEST-019 `/settings` view, direct edit compatibility, preview, confirm, rejection and unauthorized coverage is implemented
- **PARTIAL**: Live Telegram acceptance passed with ordinary-message ingestion, 43 registered commands, scoped settings confirmation, OpenAI-backed summary/search/ask, redaction, document export and confirmed forget; retention after real elapsed age remains unverified
- **PARTIAL**: The 78% global coverage floor is only a regression signal; current external gaps are production deployment, elapsed-retention behavior, authorized live provider-pool/Telegram-DM acceptance and uncovered configuration/Telegram error branches
- **DONE**: Plan 021 Phase 1 added characterization coverage for the current provider-registry cache boundary and pre-control-plane webhook OpenAPI route set
- **DONE**: `tests/test_control_plane_contract.py --runxfail` reproduced five deliberate red contracts for the reviewed route/capability matrix, cursor bounds, safe error/conflict envelopes, optimistic versions and credential redaction; normal pytest keeps them as strict `xfailed` gates until their later implementation slices remove the markers
- **DONE**: Plan 021 Phase 1 focused verification produced 32 passed and 5 expected failures; the fresh full branch-coverage run produced 231 passed, 1 skipped, 5 expected failures and 82.41% coverage, with one non-fatal aiosqlite worker-thread/event-loop teardown warning retained as a recurrence watch item
- **DONE**: Plan 021 Phase 2 historically established frozen install, pnpm-managed Node 24.18.0, peer resolution, Oxfmt, type-aware Oxlint/tsgolint, Nuxt typecheck, 5 unit/component tests, 4 Playwright checks across desktop Chromium and Pixel 7, deterministic OpenAPI/type drift, static generation and an 18-asset generated-secret scan; its `ssr: false` deployment boundary was intentionally superseded after Phase 4
- **DONE**: Plan 021 Phase 2 fresh repository verification passed Ruff format/lint, `ty`, disposable empty-database Alembic upgrade/check and 235 passed, 1 skipped, 5 expected failures at 82.44% branch coverage; the earlier aiosqlite teardown warning did not recur
- **DONE**: Plan 021 Phase 3 focused tests cover fail-closed settings, revision 0013, Mini App HMAC/freshness/duplicates, fixed-RS256 OIDC/PKCE/nonce/time validation, encrypted one-time transactions, hash-only sessions/CSRF, expiry/rotation/account switching, bootstrap/managed/delegated/native authorization, immediate grant invalidation, auth API origin/CSRF/cookies/headers/rates/audit and Nuxt browser/Mini App session choice
- **DONE**: Plan 021 Phase 3 fresh repository verification passed `uv lock --check`, Ruff format/lint, `ty`, disposable empty-database Alembic upgrade/check and the complete branch-coverage suite with 257 passed, 1 skipped, 5 expected failures and 82.88% coverage; frontend verification passed Oxfmt, type-aware Oxlint/tsgolint, raw `pnpm typecheck`, 9 unit/component tests, deterministic OpenAPI drift, six-route static generation, a 22-text-asset generated-secret scan and 6 Playwright checks across desktop Chromium and Pixel 7
- **PARTIAL**: Plan 021 provider-pool TEST-009 through TEST-015, TEST-020 and the Phase 5 provider portion of TEST-023 are implemented offline across classifier, retry, traversal, concurrency, restart persistence, cache invalidation, telemetry, cooldown/recovery, outbox/current-admin DM delivery, safe bot-admin API and Nuxt page regressions. Remaining tests cover scoped product APIs/pages, complete accessibility/e2e behavior, live Caddy/split-container lifecycle, Docker backup/restore and authorized live OIDC/provider/Telegram acceptance.

### Required Verification Commands

Run these after every implementation pass:

```powershell
uv sync --extra dev --extra local
uv run ruff format --check .
uv run ruff check .
uv run ty check
uv run pytest -q --cov=pzp_ai_bot --cov-branch --cov-report=term-missing
uv run alembic upgrade head
uv run alembic check
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
- **DONE**: Provider failure behavior is tested, including message/query embedding rollback, retryable summary claims, one safe usage row per failed call, raw-sentinel exclusion and release-before-telemetry transaction ordering

### Gate C - Private Telegram Beta

Required before inviting a real private group:

- **DONE**: Live polling in the disposable `test pzp-ai` supergroup registered 43 commands and passed policy setup, ordinary-message ingestion, OpenAI-backed summarize/search/ask, safe sensitive-data handling, settings confirmation, Markdown export and confirmed forget on 2026-07-30 with 19 AI operations and no AI errors
- **DONE**: BotFather privacy-mode/admin setup is documented with exact steps in `docs/setup.md`
- **DONE**: Telegram reported `can_read_all_group_messages=true`, the bot was a group administrator and ordinary non-command messages were ingested in the disposable group
- **DONE**: Forget behavior, retention behavior and destructive-operation audit expectations are documented
- **PARTIAL**: Confirmed `/forget scope=this_chat` removed every test message, knowledge item and embedding while preserving audit and AI-usage rows; retention after real elapsed age remains unverified
- **DONE**: Export and forget workflows are tested offline with a representative imported-memory dataset
- **DONE**: Admin policy behavior is tested with configured bot admins and mocked Telegram chat admins
- **DONE**: Cross-chat retrieval is disabled by default, explicitly query-permissioned for every destination/source pair used in beta and still requires each source scope's own learning policy
- **DONE**: Logs are structured and do not leak secrets
- **DONE**: The former single bot-image topology was rebuilt on Docker Desktop 4.84.0, fixed to install the project non-editably, and passed image `doctor`, empty-schema migration, Compose `doctor` and Docker healthcheck acceptance on 2026-07-30; this is historical evidence for the bot image, not acceptance of the new split topology

### Gate D - v1 Release

Required before calling this v1 complete:

- **DONE**: All Phase 1, Phase 2, Phase 3, Phase 4, Phase 5 and Phase 6 tasks are complete
- **PARTIAL**: The frozen non-editable bot image has current historical doctor/migration/non-root evidence, while the new independent Nuxt SSR image and Caddy/webhook Compose topology have static/focused coverage but still require live build, route, health, TLS, migration and independent-replacement acceptance
- **DONE**: CI workflow pins uv 0.12.0, rejects lock drift, performs a frozen sync, then runs formatting, linting, type checking, the 78% branch-coverage gate, migration checks, CLI checks and Docker smoke on GitHub Actions; the owner confirmed both hosted jobs green on 2026-07-30
- **DONE**: Migration history is stable, tested from an empty database through `0012_telemetry_scope_indexes`, and compared with live SQLAlchemy metadata in CI; the SQLite expression index is asserted directly because the dialect cannot reflect it for autogenerate comparison
- **DONE**: Memory quality is covered by a representative imported dataset test using summarize, ask, export and forget
- **DONE**: Privacy and operational documentation is complete
- **DONE**: Manual runbook covers install, upgrade, backup, restore, token rotation, provider switching, prompt/template switching, reloadless settings, `/settings` and emergency forget
- **PARTIAL**: v1 bot behavior, local automated gates, hosted CI, the historical bot-image acceptance, local embeddings, live OpenAI, the disposable-group functional suite and offline classified provider-pool/alert behavior are complete. Production readiness still lacks elapsed-retention validation, authorized live multi-key/Telegram-DM acceptance and live acceptance of the split Caddy/Nuxt SSR/webhook deployment.

### Gate E - Secure Control Plane And Resilient Provider Operations

Required before enabling Plan 021 in a production deployment:

- **TODO**: Every Plan 021 implementation task, security requirement, offline test and completion criterion is green
- **TODO**: Real Telegram browser OIDC and Mini App authentication pass against registered production-like HTTPS URLs, including session revocation, account switching, scope authorization and step-up reveal denial/success
- **TODO**: A disposable multi-key provider profile proves sticky selection, classified failover, cooldown/recovery, pool exhaustion and deduplicated sanitized bot-admin DM notification without exposing credentials
- **TODO**: Nuxt SSR passes responsive browser and Telegram Web/Mobile/Desktop acceptance through the real Caddy HTTPS origin, and reserved FastAPI API/auth/webhook/health paths plus credential-header removal are verified against the running proxy rather than only artifact tests
- **TODO**: Encrypted database backup/restore and master-key rotation/recovery are rehearsed, while losing the referenced master key is documented as unrecoverable
- **TODO**: Production deployment remains deferred until Gate E and the existing deployment-specific secret injection, sizing and elapsed-retention checks are all accepted

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
docker build --tag pzp-ai:local .
docker build --tag pzp-ai-web:local ./web
docker run --rm -e AI_PROVIDER=fake -e DB_URL=sqlite+aiosqlite:////tmp/pzp/bot.db -e MEMORY_DIR=/tmp/pzp/memory pzp-ai:local pzp-bot doctor
docker run --rm -e AI_PROVIDER=fake -e DB_URL=sqlite+aiosqlite:////tmp/pzp/bot.db -e MEMORY_DIR=/tmp/pzp/memory pzp-ai:local pzp-bot migrate
docker compose run --rm bot pzp-bot doctor
docker compose up -d
docker compose ps
docker compose down
```

The image commands verify the bot and frontend build boundaries without starting Telegram polling. The Compose commands exercise the production webhook/Caddy/Nuxt SSR topology and require deployment-like Telegram, OIDC, webhook, master-key and public-origin settings before startup; inspect Caddy routing, service health and logs before accepting the stack.

### Cloud AI Provider

```env
AI_PROVIDER=openai_compatible
OPENAI_API_KEY=<provider-key>
OPENAI_BASE_URL=https://api.openai.com/v1
CHAT_MODEL=<configured-chat-model>
REASONING_LEVEL=medium
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
- Effective settings resolve from environment/runtime defaults through global, chat and topic overlays. Configured bot admins may target `all`, a chat or a topic from bot DMs; ordinary Telegram chat admins may target only the current chat they administer.
- File and environment values remain the bootstrap and deployment-secret source of truth; deployment master keys and immutable bootstrap secrets stay external while encrypted provider credentials may be database-managed
- Non-secret runtime choices such as learning default, provider profile, model, reasoning level, memory top-k, batch sizes, summary cadence and retention defaults are editable without a process restart after validation
- Provider secrets must not be pasted through Telegram groups or bot DMs. Multiple managed credentials, browser/Mini App create/reveal/delete and restartless profile overlay already exist; classified rotation and sanitized best-effort bot-admin DM alerts remain Plan 021 Phase 5 work.

## 9. File Ownership Map

- **PROJECT-001**: `pyproject.toml`, `uv.lock`, `.env.example`, `.gitignore`, `.dockerignore`, `README.md`, `Dockerfile`, `web/Dockerfile`, `web/.dockerignore`, `Caddyfile`, `docker-compose.yml`, `.github/workflows/ci.yml`
- **RUNTIME-001**: `src/pzp_ai_bot/main.py`, `src/pzp_ai_bot/app_context.py`, `src/pzp_ai_bot/config.py`, `src/pzp_ai_bot/logging.py`
- **TELEGRAM-001**: `src/pzp_ai_bot/telegram/admin.py`, `src/pzp_ai_bot/telegram/commands.py`, `src/pzp_ai_bot/telegram/dispatcher.py`, `src/pzp_ai_bot/telegram/middleware.py`, `src/pzp_ai_bot/telegram/router.py`, `src/pzp_ai_bot/telegram/routers/__init__.py`, `src/pzp_ai_bot/telegram/routers/shared.py`, `src/pzp_ai_bot/telegram/routers/learning.py`, `src/pzp_ai_bot/telegram/routers/operations.py`, `src/pzp_ai_bot/telegram/routers/settings.py`, `src/pzp_ai_bot/telegram/routers/retrieval.py`, `src/pzp_ai_bot/telegram/routers/memory.py`, `src/pzp_ai_bot/telegram/safe_api.py`, `src/pzp_ai_bot/telegram/scheduler.py`, `src/pzp_ai_bot/telegram/utils.py`, `src/pzp_ai_bot/telegram/polling.py`, `src/pzp_ai_bot/telegram/webhook.py`
- **MEMORY-001**: `src/pzp_ai_bot/memory/service.py`, `src/pzp_ai_bot/memory/contracts.py`, `src/pzp_ai_bot/memory/transfer.py`, `src/pzp_ai_bot/memory/provider_operations.py`, `src/pzp_ai_bot/memory/settings_profiles.py`, `src/pzp_ai_bot/memory/retrieval_service.py`, `src/pzp_ai_bot/memory/mutation_service.py`, `src/pzp_ai_bot/memory/summary_service.py`, `src/pzp_ai_bot/memory/prompts.py`, `src/pzp_ai_bot/memory/canonical.py`, `src/pzp_ai_bot/memory/policy.py`, `src/pzp_ai_bot/memory/retrieval.py`, `src/pzp_ai_bot/memory/markdown.py`, `src/pzp_ai_bot/memory/scope.py`, `src/pzp_ai_bot/memory/vector.py`
- **AI-001**: `src/pzp_ai_bot/ai/protocols.py`, `src/pzp_ai_bot/ai/fake.py`, `src/pzp_ai_bot/ai/openai_compatible.py`, `src/pzp_ai_bot/ai/local.py`, `src/pzp_ai_bot/ai/factory.py`, `src/pzp_ai_bot/ai/redaction.py`
- **WEB-001**: `.node-version`, `web/package.json`, `web/pnpm-lock.yaml`, `web/pnpm-workspace.yaml`, `web/nuxt.config.ts`, `web/app/`, `web/openapi/`, `web/scripts/`, `web/test/`, `web/e2e/`, `web/vitest.config.ts`, `web/playwright.config.ts`
- **DB-001**: `src/pzp_ai_bot/db/models.py`, `src/pzp_ai_bot/db/repositories.py`, `src/pzp_ai_bot/db/repos/__init__.py`, `src/pzp_ai_bot/db/repos/shared.py`, `src/pzp_ai_bot/db/repos/messages.py`, `src/pzp_ai_bot/db/repos/knowledge.py`, `src/pzp_ai_bot/db/repos/canonical.py`, `src/pzp_ai_bot/db/repos/embeddings.py`, `src/pzp_ai_bot/db/repos/policy.py`, `src/pzp_ai_bot/db/repos/settings.py`, `src/pzp_ai_bot/db/repos/profiles.py`, `src/pzp_ai_bot/db/repos/challenges.py`, `src/pzp_ai_bot/db/repos/events.py`, `src/pzp_ai_bot/db/session.py`, `alembic/env.py`, `alembic/versions/0001_initial.py`, `alembic/versions/0002_phase2_memory_quality.py`, `alembic/versions/0003_phase2_remaining_memory_tools.py`, `alembic/versions/0004_phase3_provider_ai_hardening.py`, `alembic/versions/0005_phase5_operations_settings.py`, `alembic/versions/0006_phase6_product_completeness.py`, `alembic/versions/0007_align_telegram_identifier_types.py`, `alembic/versions/0008_message_logical_uniqueness.py`, `alembic/versions/0009_summary_claims.py`, `alembic/versions/0010_remove_cross_scope_can_learn.py`, `alembic/versions/0011_canonical_scope_snapshots.py`, `alembic/versions/0012_telemetry_scope_indexes.py`
- **DOCS-001**: `docs/setup.md`, `docs/architecture.md`, `docs/commands.md`, `docs/privacy.md`, `docs/deployment.md`, `docs/operations.md`, `docs/secrets.md`, `docs/release.md`, `docs/testing.md`
- **TESTS-001**: `tests/conftest.py`, `tests/test_admin_service.py`, `tests/test_ai_providers.py`, `tests/test_architecture_facades.py`, `tests/test_cli.py`, `tests/test_config.py`, `tests/test_control_plane_contract.py`, `tests/test_deployment_artifacts.py`, `tests/test_frontend_workspace.py`, `tests/test_memory_correctness.py`, `tests/test_memory_quality.py`, `tests/test_memory_service.py`, `tests/test_memory_tools.py`, `tests/test_operations.py`, `tests/test_policy_and_ingestion.py`, `tests/test_product_controls.py`, `tests/test_scope.py`, `tests/test_telegram_commands.py`, `tests/test_telegram_integration.py`, `tests/test_telegram_live.py`, `tests/test_webhook.py`

## 10. Risks And Assumptions

- **RISK-001**: Telegram privacy mode or missing admin permissions can make live testing look broken even when code works
- **RISK-003**: Provider costs and privacy risks can grow quickly if large raw chats are summarized by external models
- **CLOSED-004**: Live `gpt-5.6-terra`/medium generation and `text-embedding-3-small` embeddings passed directly, then the same provider configuration passed the disposable-group summarize/search/ask suite with no AI errors
- **RISK-005**: Bulk import atomicity is intentionally per 100-row checkpoint, so operators must inspect content-free checkpoint/failure events and retry the same input after a later parse, provider or write failure rather than assuming whole-file rollback
- **RISK-005**: Current memory compaction performs deterministic heuristic deduplication but can still miss deeper semantic duplicates or keep low-value facts
- **RISK-006**: Runtime scheduling has focused tests, but true long-running polling/webhook behavior can still differ under real Telegram load
- **RISK-007**: Optional local embeddings can pull large model dependencies and still need explicit deployment sizing; the multilingual MiniLM model is now cached and passed online plus forced-offline semantic ordering acceptance at 384 dimensions
- **CLOSED-008**: Live Telegram API, permission, command, ordinary-message, AI generation, export and forget behavior passed in the disposable administrator-controlled supergroup; sustained real load remains covered separately by RISK-006
- **RISK-009**: Cross-chat retrieval can leak context across communities if destination query permissions are misconfigured or tested only on synthetic scopes
- **CLOSED-010**: Runtime prompt/provider changes remain attributable because selected templates, profiles, models and reasoning levels are logged through one safe provider-call telemetry record; failed calls commit only controlled metadata and bounded error summaries after the business rollback or claim release
- **RISK-011**: `/settings` preview/confirmation reduces broad-change surprises, but concurrent admins can still preview overlapping scoped changes and apply the latest active token
- **CLOSED-012**: The owner confirmed both hosted GitHub Actions jobs green on the pushed branch
- **RISK-013**: Telegram Desktop JSON exports and Bot API update dumps can contain historical group content that predates bot consent; operators must review export files and target scopes before import
- **RISK-014**: Historical Docker smoke proves bot-image startup, health checks and migrations, but the new frontend image plus Caddy/webhook topology still needs live routing, health, TLS, independent-replacement, production sizing and secret-injection validation
- **RISK-015**: A selected provider profile still has a single-key failure domain. The planned pool must rotate among multiple external secret references without exposing key material, distinguish invalid requests from authentication/quota/rate-limit/provider failures, serialize concurrent health transitions and debounce sanitized bot-admin DM alerts; admins must first open the bot DM because Telegram bots cannot initiate a conversation with a user who has never contacted them.
- **RISK-016**: Full secret reveal intentionally increases impact if a bot-admin session is stolen; Plan 021 therefore requires fresh Telegram step-up, no-store responses, revocable server-side sessions, audit attribution and a deployment option to disable reveal without removing write/replace operations
- **RISK-017**: Telegram OIDC, Mini App Web APIs and client cookie behavior can drift independently; implementation must re-read discovery/current official docs and pass real Web, Desktop and Mobile client checks rather than copying the older HandleHub popup shortcut
- **RISK-018**: A dashboard can create unbounded memory/telemetry queries or Caddy route shadowing; every list/export requires pagination or streaming, every aggregation requires measured SQL, and reserved API/auth/webhook/health routes plus frontend credential-header removal require live ingress verification
- **CLOSED-001**: Global review IDs no longer let a Telegram admin act on another chat's review because approval and rejection authorize the persisted owning scope before mutation; same-chat and configured-bot-admin access remain covered by dispatcher tests
- **CLOSED-002**: Daily Markdown no longer receives pre-commit appends or pending review proposals; it is rebuilt from committed knowledge after mutations and startup, so rejection, deletion, retention and forget operations remove projected content while audit metadata remains
- **CLOSED-003**: Failed or empty summary generation no longer synthesizes durable raw-chat content or marks source messages summarized; messages remain retryable and diagnostics retain only safe provider/model/source-ID/exception-type metadata
- **CLOSED-004**: Idle-summary and runtime-settings loops now survive ordinary iteration exceptions with bounded backoff, expose typed health, shut down safely in every task state and make webhook health return HTTP 503 if a critical task dies
- **CLOSED-005**: Missing, mistyped or unrelated `/allow_admins` values no longer fall through to `false`; authorization runs first and the shared strict parser rejects malformed consent input before any mutation
- **CLOSED-006**: CI now upgrades and checks the same isolated database against live SQLAlchemy metadata, and revision `0007_align_telegram_identifier_types` resolves the nine real `Integer`/`BigInteger` mismatches exposed when the gate first ran
- **CLOSED-011**: Retrieval no longer performs per-candidate database awaits or unbounded hydration; each authorized source scope uses four SQL statements, a configurable 5,000-candidate default, deterministic durable-first admission and focused recall/query-count regression coverage
- **CLOSED-013**: CI and Docker no longer resolve mutable dependency sets: both pin uv 0.12.0, CI checks and frozen-syncs `uv.lock`, and Docker uses two frozen production syncs around the source copy
- **CLOSED-016**: CI no longer runs pytest without a regression signal; the full package is measured with branch coverage and visible missing lines against a conservative 78% floor below the measured 81% starting baseline
- **CLOSED-017**: Learning decisions and multi-scope status no longer reload the full policy table for each rule or scope; one immutable transaction-local snapshot preserves precedence and reason semantics while avoiding stale cross-request caching
- **CLOSED-007**: Main-chat `NULL` no longer bypasses message uniqueness: revision 0008 reconciles duplicate messages/embeddings, an expression index canonicalizes the logical thread key and atomic insert-or-fetch resolves concurrent redeliveries to one row
- **CLOSED-008**: Summary work no longer sends an unbounded backlog through one provider request, processes the same sources concurrently or holds a SQLite session across provider latency; revision 0009 persists bounded claims with retryable failure, stale recovery and idempotent finalization
- **CLOSED-009**: Cross-scope permissions no longer advertise a no-op learning control; revision 0010 discards never-consumed `can_learn` values while preserving `can_query`, and the command rejects obsolete or unknown keys before persistence
- **CLOSED-012**: Canonical compaction cost now scales with concrete dirty scopes instead of all stored scopes; validated SQLite snapshots are derived from exhaustive output fingerprints, and drift, corruption, failure, last-item deletion and forced full repair are covered without making cached Markdown authoritative
- **CLOSED-018**: AI usage status no longer materializes an unbounded table or treats chat scope as exact-only; normalized scope columns and SQL aggregates preserve global/chat/topic hierarchy, legacy invalid values remain safely unscoped, and query-plan regressions cover the new append-only audit indexes

- **ASSUMPTION-001**: The project remains Python 3.12 and `uv` managed
- **ASSUMPTION-002**: v1 remains RAG memory, not online fine-tuning
- **ASSUMPTION-003**: Local development uses `AI_PROVIDER=fake`
- **ASSUMPTION-004**: Real deployments will use either OpenAI-compatible chat/embeddings or local embeddings through the provider interface
- **ASSUMPTION-005**: The default storage remains SQLite plus Markdown until the project outgrows a single-node deployment
- **ASSUMPTION-006**: Telegram command and DM settings remain limited to non-secret values. Plan 021 is the dedicated secret-management workflow, and it is browser/Mini-App-only even after implementation.

## 11. References

- Telegram Bot API: https://core.telegram.org/bots/api
- Telegram bot features and privacy mode: https://core.telegram.org/bots/features
- Telegram Mini Apps: https://core.telegram.org/bots/webapps
- Telegram Login and OpenID Connect: https://core.telegram.org/bots/telegram-login
- aiogram documentation: https://docs.aiogram.dev/
- OpenAI embeddings guide: https://platform.openai.com/docs/guides/embeddings
- Nuxt 4 documentation: https://nuxt.com/docs/4.x/
- Nuxt UI documentation: https://ui.nuxt.com/
- `uv` documentation: https://docs.astral.sh/uv/
- `ruff` documentation: https://docs.astral.sh/ruff/
- `ty` documentation: https://docs.astral.sh/ty/
- `ijson` package source: https://pypi.org/project/ijson/
- Inspiration project: https://github.com/lyzcoote/go-shitfuckllm

## 12. Changelog

- **2026-08-01 / v0.43**: Completed Plan 021 Phase 6 with reloadless managed bot owners and delegated chat admins, immediate session/Telegram-command authority updates, requester-bound prepared/DM group enrollment, revision 0016 lifecycle normalization, active-only ingestion/idle summarization, distinct pause/resume/detach/leave/forget behavior, safe runtime and inherited scoped-setting APIs, synchronized OpenAPI types and SSR Bot Settings/Chats pages. Final gates passed 355 Python tests with 1 live skip and 4 strict xfails, fresh migration/drift through 0016, Ruff/ty/CLI/doctor, 30 Nuxt tests, 236 generated text assets, direct SSR smoke and all 22 desktop/mobile Playwright checks; Phase 7 is next, while live Telegram prepared-button/OIDC, split Compose/Caddy/TLS, authorized provider failover/admin-DM, backup/restore and elapsed-retention acceptance remain release gates.
- **2026-08-01 / v0.42**: Fixed combined-development `.env` propagation after real `pnpm dev` exposed that Pydantic had loaded the dedicated UID/origin/port without exporting them to `os.environ`, while the development builder read only the process environment. The dev-only values now flow through `Settings`, remain inert for normal polling/webhook startup and have a real dotenv regression; the final checkpoint passed 52 focused development/CLI tests, full Ruff format/lint, `ty` and a bounded offline `pnpm dev` health/proxy/session smoke using the UID from the repository `.env`.
- **2026-08-01 / v0.41**: Added a complete combined loopback development topology: `pzp-bot control-plane-dev` builds the normal context with command-scoped localhost/UID settings, real persisted session/CSRF/capability/audit behavior, exact-action development step-up, a stable ignored local vault key and Telegram-offline default with fail-closed optional HTTPS webhook connectivity. `web/pnpm dev` now coordinates the uv backend and Nuxt through stable PowerShell 7 process-tree roots on Windows, prefix-preserving `/api/control`, `/auth`, `/telegram/webhook` and `/health` proxies, automatic development login after the exact anonymous 401 and production artifact exclusion. Live Chromium acceptance also corrected broad `/api` interception and environment bootstrap-pool UX; final gates passed 343 Python tests with 1 live skip and 4 strict xfails, 27 Nuxt tests, 227 scanned assets, direct SSR smoke and all 16 desktop/mobile Playwright checks. Phase 5 is validated and Phase 6 is ready/not started; connected live Telegram, split Docker/Caddy/TLS/OIDC/provider and backup/restore acceptance remain Gate E work.
- **2026-08-01 / v0.40**: Completed Plan 021 Phase 5 with typed opaque provider-call policy, exact OpenAI SDK failure classification, stable bounded sticky environment/managed credential pools, same-key retry, cooldown/hard-disable/recovery and cross-instance version coordination, revision-aware managed-client invalidation, pooled chat/embedding execution and safe attempt telemetry. Added revision 0015 deduplicated admin-alert intents/deliveries, current-effective-admin Telegram DM supervision, bot-admin pool/manual-test/alert APIs, synchronized OpenAPI types and an SSR-safe Nuxt Providers console with explicit paid/download warnings. Final verification passed 303 Python tests with 1 live skip and 4 strict expected failures, 12 Nuxt tests, 20 scanned assets, direct provider SSR smoke and 14 desktop/mobile Playwright checks; live provider/Telegram and split-deployment acceptance remain Gate E work, and Phase 6 awaits user validation.
- **2026-08-01 / v0.39**: Replaced the generated static SPA deployment with Nuxt SSR in an independent non-root Node image behind exact-pinned Caddy and the private webhook FastAPI service. Added client-only auth/session guards, neutral protected-page and auth-dependent shell hydration boundaries, private no-store documents, fresh per-response CSP nonces on every generated inline script without `script-src 'unsafe-inline'`, immutable assets, a bounded production SSR/secret smoke, JavaScript-disabled direct-route coverage, three-service Compose/Caddy routing with frontend credential-header removal, independent frontend/backend CI image builds and reconciled architecture/deployment/security/testing/release roadmaps. Final non-Docker verification passed 11 Nuxt unit tests, 17 scanned assets, 10 desktop/mobile Playwright checks and 272 Python tests with 1 skip and 4 strict expected failures; live image build, Caddy routing/TLS/health, Compose restart/replacement and Telegram webhook acceptance remain deferred because Docker is unavailable, and Plan 021 Phase 5 has not started.
- **2026-08-01 / v0.38**: Completed Plan 021 Phase 4 with strict master-key preflight, a domain-separated AES-256-GCM envelope vault, revision 0014 managed profiles/credentials/pool state, environment/bootstrap overlay precedence, reloadless provider CRUD, optimistic versions, exact-action browser/Mini App step-up, audited non-cacheable reveal with component-memory lifecycle clearing and transaction-safe counts-only vault status/rewrap operations. The final checkpoint passed with 269 Python tests, 1 skip, 4 expected failures and 82.83% branch coverage plus every static, migration, OpenAPI, generated-asset, Nuxt unit and 6 desktop/mobile browser gate; automatic classified key-pool failover, health transitions and alerts remain Phase 5 work.
- **2026-07-31 / v0.37**: Completed only Plan 021 Implementation Phase 3 before the required Phase 4 validation pause. Added fail-closed control-plane bootstrap settings, revision 0013 identity/session/OIDC/admin/managed-chat persistence, strict signed Mini App validation, bounded Telegram OIDC Authorization Code/PKCE/JWKS with fixed RS256 verification, AES-GCM-protected one-time transactions, opaque hash-only sessions/CSRF, shared live capability resolution, auth/session/step-up APIs, same-origin/CSRF/security-header/rate/audit controls and Nuxt browser/Mini App session UX using the current Web App script. Control API calls now fail within five seconds when a local backend is absent instead of leaving the SPA on its opening state, and Playwright uses the declared pnpm dev-server script plus deterministic auth stubs; the final checkpoint is 257 passed, 1 skipped, 5 expected failures and 82.88% Python coverage, 9 frontend unit/component tests, 6 desktop/mobile browser checks, six generated routes and 22 scanned text assets. Provider-vault preflight, managed provider credentials/pools, privileged product routes and production static serving remain unimplemented.
- **2026-07-30 / v0.36**: Completed only Plan 021 Implementation Phase 2 before the required Phase 3 validation pause. Added the exact-pinned Nuxt 4/Nuxt UI client workspace with pnpm-managed Node 24.18.0, Oxc plus tsgolint gates, accessible responsive shell, Telegram client/safe-area integration, same-origin development API boundary, deterministic OpenAPI types, frozen frontend CI, Docker-context exclusions and generated-asset secret scanning; frontend gates, 4 desktop/mobile Playwright checks, Alembic drift and the 235-pass 82.44% Python checkpoint are green, while authentication, control API/static serving, migrations, vault and provider pools remain unimplemented.
- **2026-07-30 / v0.35**: Began approved Plan 021 and completed only Implementation Phase 1 before the required user-validation pause. Recorded drift through `d501341`, migration head `0012`, current service/provider/admin/runtime/OpenAPI boundaries, strict red control-plane contracts, the Bot API 10.2/aiogram 3.30 auth and `requestChat` baseline, exact Node/Nuxt/Oxc/security-library pins and the TypeScript 6 compatibility decision; no Nuxt workspace, auth service, database migration, secret vault or provider-pool production code is claimed.
- **2026-07-30 / v0.34**: Added review-ready Plan 021 for encrypted multi-key provider pools and a Telegram-authenticated Nuxt control plane. Recorded Option A audited full reveal for database-managed provider keys, current Telegram OIDC Authorization Code plus PKCE, signed Mini App `initData`, same-origin static Nuxt/FastAPI architecture, scoped bot/chat administration, managed-chat lifecycle, query/memory/stats/audit surfaces and explicit offline/live/deployment gates; no application implementation is claimed.
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
- **2026-06-24 / v0.12**: Patched Codex Security scan findings: target-scope authorization for Telegram commands, bot-admin-only global profile/runtime/template mutations, fail-closed webhook default/placeholder secret rejection, quoted JSON secret redaction, DB URL credential masking in CLI diagnostics, independent subagent verification and focused/full validation gates.
- **2026-07-29 / v0.13**: Closed cross-chat review-decision authorization: `/memory_approve` and `/memory_reject` now resolve each pending review's persisted owning scope before mutation, with service-level lookup coverage and dispatcher tests for foreign-chat denial, same-chat access, configured-bot-admin access and unknown review IDs.
- **2026-07-29 / v0.14**: Made SQLite authoritative for durable memory and replaced append-only daily Markdown writes with deterministic post-commit and startup reconciliation; pending reviews remain SQLite-only, privacy lifecycle operations remove projected content, and content-free projection failures remain auditable.
- **2026-07-29 / v0.15**: Made summary generation failures explicit and retryable: provider exceptions and empty responses now return zero completed messages, preserve pending sources, create no knowledge/review/Markdown content and record content-free failure telemetry; fail-once retry coverage includes review mode on and off.
- **2026-07-29 / v0.16**: Added cancellation-safe supervision for idle-summary and runtime-settings loops with typed health state, bounded exponential backoff, content-free diagnostics, hardened shutdown and webhook HTTP 503 health when either critical task is dead.
- **2026-07-29 / v0.17**: Made `/allow_admins` consent parsing fail closed by reusing the strict boolean parser; missing, mistyped and unrelated values now return usage without mutation, while explicit on/off and authorization behavior remain covered.
- **2026-07-29 / v0.18**: Added an isolated-database `alembic check` CI gate, repaired the nine Telegram identifier type mismatches it exposed through revision `0007_align_telegram_identifier_types`, and added artifact plus runtime schema regression coverage.
- **2026-07-29 / v0.19**: Bounded SQLite retrieval with deterministic durable-first candidate admission, one-time query normalization and batch hydration, reducing the measured 1,000/10,000-candidate query counts from 1,001/10,001 to four; OpenAI-compatible chat now also forwards the selected environment/runtime/scope/one-shot reasoning level as `reasoning_effort` for recognized reasoning-model IDs.
- **2026-07-29 / v0.20**: Pinned uv 0.12.0 consistently in CI and Docker, made CI reject lock drift before a frozen development sync, and changed the Docker dependency layers to install the exact locked production environment through frozen syncs before and after source copy.
- **2026-07-29 / v0.21**: Completed the canonical CLI reference for all registered Typer commands with operational cautions and canonical-document links, and added a live command-inventory regression so future undocumented commands fail artifact tests by name.
- **2026-07-29 / v0.22**: Turned the existing pytest-cov configuration into a conservative CI regression gate with branch measurement, visible missing lines and a 78% floor below the measured 81% starting baseline; documented that critical-path behavior remains more important than percentage chasing.
- **2026-07-29 / v0.23**: Replaced repeated learning-policy table scans with one immutable transaction-local snapshot per decision, standalone status or multi-scope report; query-count tests reduced an enabled decision from four policy SELECTs and six policy-only scopes from thirteen to one while preserving precedence and reason strings.
- **2026-07-29 / v0.24**: Enforced concurrent Telegram-message idempotency with an authoritative SQLite expression index over the logical scope key, atomic insert-or-fetch repository behavior and revision 0008 reconciliation of pre-existing duplicate messages plus provider/model embeddings.
- **2026-07-29 / v0.25**: Bounded summary provider requests with persisted atomic claims, stale recovery and compare-and-set finalization; moved AI provider work outside database sessions, made manual/ingest/idle backlog limits explicit and added revision 0009 plus concurrency, failure-atomicity and transaction-boundary regressions.
- **2026-07-29 / v0.26**: Removed the advertised but unused cross-scope `can_learn` API and column through revision 0010, preserved destination query permissions and source learning-policy enforcement, rejected obsolete/unknown command keys before mutation and corrected active roadmap terminology to retrieval-only profiles.
- **2026-07-29 / v0.27**: Unified every chat and embedding call behind an immutable safe telemetry record, persisted failed-call usage only after business rollback or summary-claim release in a separate short transaction, excluded raw provider exception text and added message/query/summary failure-atomicity regressions; full verification passed with 202 tests, 1 skipped and 81.38% branch coverage.
- **2026-07-29 / v0.28**: Added revision 0011 and validated per-scope canonical snapshots with exhaustive item/provider/prompt fingerprints, dirty-scope mutation wiring, zero-call unchanged reuse, forced rebuilds, concurrent-drift retries, last-valid failure retention, corrupt-row recovery and deterministic stale-scope removal; migration drift, static checks and full verification passed with 207 tests, 1 skipped and 81.59% branch coverage.
- **2026-07-29 / v0.29**: Added revision 0012 with normalized AI usage scope and composite usage/event indexes, safely backfilled supported legacy scope metadata, moved status counts/errors/latency/operation totals into SQL with correct chat-topic hierarchy and verified representative SQLite query plans; full verification passed with 211 tests, 1 skipped and 81.53% branch coverage.
- **2026-07-29 / v0.30**: Streamed JSONL and supported Telegram JSON imports with pinned `ijson==3.5.1`, 512 MiB/100,000-row safeguards, retry-safe independently audited 100-row checkpoints and provider work outside write transactions; added primary-key export paging, batched scope/source hydration, incremental CLI writes and UTF-8-safe Telegram document streaming. Frozen lock, Ruff, ty and 125 focused transfer tests passed; full verification passed with 218 tests, 1 skipped and 81.38% branch coverage.
- **2026-07-29 / v0.31**: Began the behavior-preserving Plan 020 hotspot split: repository implementations moved under cohesive `db.repos` modules behind the unchanged `db.repositories` facade; shared memory contracts, streaming transfer and provider-operation/telemetry logic moved behind `MemoryService` delegates; architecture and compatibility inventory tests now record the landed boundaries. Whole-project Ruff format/lint and `ty` passed, and the fresh offline checkpoint passed with 222 tests and 1 skipped; Telegram router families and the remaining MemoryService slices remain explicitly pending.
- **2026-07-30 / v0.32**: Completed Plan 020 by extracting all remaining MemoryService collaborators and Telegram command-family routers behind stable facades, enforced dependency direction and exact composition through architecture tests, and reconciled Plan 014's living roadmap/test/ownership/release-gate drift. Ruff, `ty`, empty-schema migration/drift checks and a fresh 228-passed/1-skipped suite at 82.43% branch coverage passed. Current Docker build/doctor/migration/Compose/health acceptance and live Telegram `getMe` passed after fixing the runtime image to install the project non-editably; live OpenAI remains blocked by account quota, the local model is not cached and disposable-group Telegram validations remain TODO.
- **2026-07-30 / v0.33**: Corrected GPT-5 Chat Completions requests to use `max_completion_tokens` without a non-default temperature, validated every approved non-Pro GPT-5 alias in the account inventory and completed direct `gpt-5.6-terra`/medium plus OpenAI embedding acceptance. Cached and forced-offline validated multilingual MiniLM, passed a disposable-group Telegram suite across policy, simple/daily/sensitive/technical ingestion, summary/search/ask, settings, export and forget, confirmed both hosted CI jobs green and completed a fresh 229-passed/1-skipped full checkpoint. Documented the exact scope/DM precedence and retained automatic provider-key failover/reload plus production deployment and elapsed-retention checks as explicit remaining work.
