# Commands

This page is the command reference for Telegram admins, configured bot admins and local operators. Commands use the current chat or forum topic unless a `scope=` argument is accepted.

## Conversation And Retrieval

- `/ask [profile=name] [provider=name] [system=name] [model=name] [reasoning=low|medium|high] [prompt="one-shot system prompt"] <question>`: answer from current chat/topic memory by default and include compact source references when the model output does not already cite them. `profile=name` expands retrieval through explicitly permissioned, learning-enabled memory profiles. `provider`, `model`, `reasoning`, `system` and `prompt` are admin-only one-shot AI overrides.
- `/memory_search [profile=name] [provider=name] [embedding_model=name] <query>`: show top retrieved memory snippets, optionally using a permissioned cross-chat profile and admin-selected query embedding provider/model.
- `/summarize_now [template=name] [provider=name] [model=name] [reasoning=low|medium|high] [prompt="one-shot instruction"]`: summarize pending learned messages for this scope with the general template, a named template or an admin-only one-shot summary instruction.
- `/summarize_range start=<message_id> end=<message_id> [template=name] [provider=name] [model=name] [prompt="one-shot instruction"]`: summarize learned messages between two boundary messages in the current scope and record the source message IDs, date interval and template name.

## Learning Policy

- `/learn_on`, `/learn_off`, `/learn_status`: control learning for the current scope. Status also shows effective memory/settings values, including provider, model, reasoning and prompt choices.
- `/allow_chat`, `/deny_chat`: enable or disable the whole chat scope.
- `/allow_topic`, `/deny_topic`: enable or disable the current topic scope.
- `/allow_user <id>` or reply with `/allow_user`: allow a user for this scope.
- `/deny_user <id>` or reply with `/deny_user`: deny a user for this scope.
- `/allow_admins on|off`: allow chat admins as learnable authors in this scope.

## Memory Management

- `/memory_status [scope=...]`: show scoped message, learned, skipped, knowledge, embedding, audit, pending-review and AI usage counts for a chat/topic or all scopes when run by a configured bot admin.
- `/memory_scopes [scope=this_chat|all|<chat_id>:<thread_id>]`: list known chat/topic scopes, labels, learning status and message/knowledge counts so admins can discover topic allowlists.
- `/memory_debug [scope=...] [message_id=N] [limit=N]`: explain why recent skipped messages were not learned without returning the skipped message text.
- `/memory_export [md|jsonl] [scope=...]`: stream a memory export with readable scope/source metadata where available. Large exports are sent incrementally as deterministic numbered document parts below the 45 MiB safety threshold and Telegram's 50 MiB upload limit. Unknown formats, scopes, unauthorized foreign chat scopes or extra tokens are rejected with a usage/error message.
- `/memory_prune [scope=...]`: apply configured raw-message and knowledge retention for a scope.
- `/forget scope=this_topic|this_chat|all confirm=<token>`: delete memory after a confirmation round trip.

## Memory Items And Notes

- `/remember [tags=a,b] [priority=N] [source=url] <text>`: add a durable manual note with optional tags, priority and source metadata.
- `/memory_notes [scope=...] [tag=name]`: list manual notes for a scope, optionally filtered by tag.
- `/memory_note_edit <id> [tags=a,b] [priority=N|off] [source=url|off] [text]`: edit manual note metadata and optional text.
- `/memory_note_delete <id>`: delete a manual note from the current scope.
- `/memory_items [scope=...] [kind=name] [limit=N]`: browse recent memory item IDs, kinds, source IDs and compact content snippets.
- `/memory_item_delete <id> [scope=...]`: delete a single memory item and its embeddings without forgetting the entire scope.
- `/memory_item_refresh <id> [mode=embed|summarize|both] [scope=...]`: refresh a single memory item embedding, or re-summarize it from recorded source messages before embedding.

## Reviews, Profiles And Templates

- `/memory_reviews [scope=...]`: list pending review proposals for scopes with review-before-compact enabled.
- `/memory_approve <review_id>`: approve a pending proposal and insert it as durable memory after authorizing against the review's persisted owning scope.
- `/memory_reject <review_id>`: reject a pending proposal after authorizing against the review's persisted owning scope.
- `/memory_profiles`: list named cross-chat memory profiles.
- `/memory_profile_set <name> sources=<scope,scope>`: create or replace a profile source scopes. This global cross-scope operation is restricted to configured bot admins.
- `/memory_profile_allow <name> [scope=...] [enabled=on|off]`: allow or revoke a destination scope's permission to query a profile's source scopes. This global cross-scope operation is restricted to configured bot admins. Cross-chat retrieval also requires each source scope's own learning policy to be enabled. `learn=` and other unsupported keys are rejected without changing permission state.
- `/memory_templates`: list built-in and custom summary templates.
- `/memory_template_set <name> <prompt>`: create or update a reusable custom summary template. Custom template writes are restricted to configured bot admins.
- `/system_prompts`: list built-in and custom answer system prompt templates.
- `/system_prompt_set <name> <prompt>`: create or update a reusable answer system prompt template. Custom prompt writes are restricted to configured bot admins.

## Settings And Admin Review

- `/memory_settings [scope=this_topic|this_chat|all|<chat_id>:<thread_id>] [top_k=N] [summary_batch_size=N] [summary_model=name] [provider=name] [chat_model=name] [embedding_model=name] [reasoning=low|medium|high] [system_prompt=name] [raw_retention_days=N|off] [knowledge_retention_days=N|off] [review_before_compact=on|off]`: view or directly edit scoped memory, provider, prompt and model settings.
- `/settings [scope=this_topic|this_chat|all|<chat_id>:<thread_id>] [top_k=N] [summary_batch_size=N] [summary_model=name] [provider=name] [chat_model=name] [embedding_model=name] [reasoning=low|medium|high] [system_prompt=name] [raw_retention_days=N|off] [knowledge_retention_days=N|off] [review_before_compact=on|off]`: preview scoped setting changes and return a confirmation token. Re-run `/settings [scope=<same-scope>] confirm=<token>` before expiry to apply and audit the change.
- `/audit_events limit=N event_type=name scope=this_topic|this_chat|all`: review audit events. `scope=all` is restricted to configured bot admins.
- `/runtime_settings [reload=on] [retrieval_candidate_limit=N] [key=value ...]`: view, edit or force a safe runtime-settings refresh from Telegram. Runtime-setting reads and writes are restricted to configured bot admins because they affect global defaults.
- `/stats`: show global message, knowledge, embedding and event counts. `/stats scope=...` shows the richer scoped status view and requires admin access.
- `/help`: show command help inside Telegram.

Configured `BOT_ADMINS` can administer every scope, including from a bot DM: `/runtime_settings` changes safe bot-wide defaults, `/settings scope=all ...` changes the global scope overlay, `/settings scope=<chat_id> ...` targets a chat and `/settings scope=<chat_id>:<thread_id> ...` targets a topic. Telegram chat admins can administer only the current chat they administer; commands that accept `scope=` reject foreign chat scopes for chat admins instead of applying current-chat authorization to another target. Effective scoped settings resolve from safe runtime defaults to the global scope, then the chat and finally the topic, so a chat setting is inherited by its topics but never by another chat. Global profile, runtime-setting, summary-template and system-prompt mutations are restricted to configured bot admins. Polling and webhook startup register the current command list with Telegram through `set_my_commands`.

## CLI Reference

- `pzp-bot migrate`: creates or upgrades the configured database schema through Alembic. This mutates the database; back up operator data and follow the migration guidance in [operations.md](operations.md) before running it against persistent state.
- `pzp-bot polling`: clears any configured webhook and starts the long-running Telegram polling runtime. Complete [setup.md](setup.md) first and use [deployment.md](deployment.md) for runtime guidance.
- `pzp-bot webhook`: registers the configured webhook and starts the long-running FastAPI webhook runtime. Configure and validate the webhook URL and secret as documented in [deployment.md](deployment.md).
- `pzp-bot control-plane-dev`: starts the dedicated loopback FastAPI control-plane development runtime for use with `web/pnpm dev`. It requires backend-only `CONTROL_PLANE_DEV_USER_ID`, accepts loopback HTTP without Telegram OIDC, performs no Telegram API calls while `WEBHOOK_URL` is empty and deliberately registers `setWebhook` only when a complete HTTPS webhook/token/secret configuration is present; see [setup.md](setup.md) before using connected mode.
- `pzp-bot summarize-now [--scope all|<chat_id>|<chat_id>:<thread_id>]`: summarizes pending learned messages in each selected concrete scope and compacts canonical memory. This can invoke the configured AI provider and incur external cost or data transfer; review [privacy.md](privacy.md) before using an external provider.
- `pzp-bot export-memory [--scope all|<chat_id>|<chat_id>:<thread_id>] [--format md|jsonl] [--out path]`: streams primary-key-ordered export chunks to a file or standard output without constructing a second complete payload. Treat either form as potentially sensitive and protect it according to [privacy.md](privacy.md).
- `pzp-bot import-jsonl --file path --chat-id <id> [--thread-id <id>]`: incrementally validates and writes durable knowledge items into the selected chat/topic scope in independent 100-row checkpoints. Imports reject files over 512 MiB or 100,000 rows; a later malformed row or provider failure leaves earlier audited batches committed, and retrying skips their exact kind/content/source tuple. Review the input, target scope, provider cost and consent implications in [privacy.md](privacy.md) before importing.
- `pzp-bot doctor`: checks configured provider profiles, missing OpenAI-compatible keys, missing local optional dependencies, SQLite parent write access and memory directory write access without starting Telegram.
- `pzp-bot show-config`: prints non-secret runtime settings including provider profiles, default system prompt, reasoning level, retrieval candidate limit and admin-cache TTL. Credential-bearing database URLs are redacted before display.
- `pzp-bot secrets status`: validates the configured master-key ring and reports configured/active IDs, encrypted credential counts, per-key reference counts and missing referenced IDs without decrypting or printing provider secrets.
- `pzp-bot secrets rewrap --to <master-key-id> [--yes]`: warns that a current database backup is required, decrypts and verifies every managed credential wrapping before any update, then atomically rewraps all DEKs under the selected configured key. Without `--yes` it asks for interactive confirmation; output contains counts and key IDs only.
- `pzp-bot memory-status --scope all|<chat_id>|<chat_id>:<thread_id>`: prints the same scoped message/memory/AI usage status available through `/memory_status`.
- `pzp-bot import-telegram-json --file result.json --chat-id <id> [--thread-id <id>]`: streams Telegram Desktop message arrays or Bot API update-dump arrays with `ijson==3.5.1`, then embeds and commits learned raw-message memory in independent 100-row checkpoints. The same 512 MiB and 100,000-row limits apply; unsupported shapes are rejected before business writes, while a later parse/provider failure preserves earlier audited batches for idempotent retry.
- `pzp-bot audit-events --limit N [--event-type name] [--scope scope] [--format text|jsonl] [--out path]`: reviews or exports `bot_events`.
- `pzp-bot runtime-settings [--reload] [--set key=value]...`: views, reloads or edits safe database-backed runtime settings, including the positive `retrieval_candidate_limit` brute-force work cap.
- `pzp-bot db-stats`: prints SQLite file size and table row counts.
- `pzp-bot db-integrity`: runs `PRAGMA integrity_check` and exits non-zero unless SQLite reports `ok`.
- `pzp-bot db-vacuum`: runs SQLite `VACUUM`.
- `pzp-bot db-backup --out path`: creates a SQLite online backup.
