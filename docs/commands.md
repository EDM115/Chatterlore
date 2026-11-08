# Commands

Commands use the current chat or forum topic unless they accept `scope=`. See [Concepts](concepts.md) for scope inheritance, roles and profiles, and [Setup](setup.md) before running the bot.
Type commands directly, including `/command@bot_username` in groups with several bots. Startup clears the default slash-command menu, the Main Mini App/menu button and custom language/chat menus remain available. The web interface covers routine administration, local imports and some advanced commands use the CLI or Telegram.  
Ordinary participants can use `/start`, `/help`, `/ask`, `/memory_search`, `/learn_status` and argument-free `/stats`, admin-only AI overrides still require admin authority. Memory management, learning changes, exports, summaries and settings require chat-scoped administration or bot ownership. Global defaults, cross-chat profile grants, custom template writes and embedding-index management require a bot owner. CLI commands trust local operator access rather than Telegram identity.  

## Conversation And Retrieval

- `/start`: show the welcome and command-help entry point in a bot DM or chat.
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

- `/memory_status [scope=...]`: show scoped message, learned, skipped, knowledge, embedding, audit, pending-review and AI usage counts for a chat/topic or all scopes when run by a bot owner.
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
- `/memory_profile_set <name> sources=<scope,scope>`: create or replace a profile source scopes. This global cross-scope operation is restricted to bot owners.
- `/memory_profile_allow <name> [scope=...] [enabled=on|off]`: allow or revoke a destination scope's permission to query a profile's source scopes. This global cross-scope operation is restricted to bot owners. Cross-chat retrieval also requires each source scope's own learning policy to be enabled. `learn=` and other unsupported keys are rejected without changing permission state.
- `/memory_templates`: list built-in and custom summary templates.
- `/memory_template_set <name> <prompt>`: create or update a reusable custom summary template. Custom template writes are restricted to bot owners.
- `/system_prompts`: list built-in and custom answer system prompt templates.
- `/system_prompt_set <name> <prompt>`: create or update a reusable answer system prompt template. Custom prompt writes are restricted to bot owners.

## Settings And Admin Review

- `/memory_settings [scope=this_topic|this_chat|all|<chat_id>:<thread_id>] [top_k=N] [summary_batch_size=N] [summary_model=name] [provider=name] [chat_provider=name] [summary_provider=name] [embedding_provider=name] [chat_model=name] [embedding_model=name] [reasoning=low|medium|high] [system_prompt=name] [raw_retention_days=N|off] [knowledge_retention_days=N|off] [review_before_compact=on|off]`: view or directly edit scoped memory, provider, prompt and model settings.
- `/settings [scope=...] [key=value ...]`: preview the same scoped settings and return a confirmation token. Re-run `/settings [scope=<same-scope>] confirm=<token>` before expiry to apply and audit the change.
- `/audit_events limit=N event_type=name scope=this_topic|this_chat|all`: review audit events. `scope=all` is restricted to bot owners.
- `/runtime_settings [reload=on] [retrieval_candidate_limit=N] [key=value ...]`: view, edit or force a safe runtime-settings refresh from Telegram. Runtime-setting reads and writes are restricted to bot owners because they affect global defaults.
- `/stats`: show global message, knowledge, embedding and event counts. `/stats scope=...` shows the richer scoped status view and requires admin access.
- `/help`: show command help inside Telegram.

Bootstrap owners in `BOT_ADMINS` and database-managed bot owners can administer every scope, including from a bot DM: `/runtime_settings` changes safe bot-wide defaults, `/settings scope=all ...` changes the global scope overlay, `/settings scope=<chat_id> ...` targets a chat and `/settings scope=<chat_id>:<thread_id> ...` targets a topic. Telegram chat admins can administer only the current chat they administer, commands that accept `scope=` reject foreign scopes instead of applying current-chat authorization to another target. Effective settings resolve from safe runtime defaults through global, chat and topic overlays. Global profile, runtime-setting, summary-template and system-prompt mutations remain bot-admin-only, clearing the visible command menu changes none of these rules.

## Independent Route Settings

Independent scoped routes accept `chat_provider=NAME`, `summary_provider=NAME` and `embedding_provider=NAME` (their full `*_provider_profile` names are also accepted) in `/memory_settings` and confirmed `/settings`. Use `inherit` to clear a route override, the UI's Reset to inherited sends the equivalent null. `/runtime_settings` and CLI `runtime-settings --set` use the full names. An embedding-affecting change on populated memory returns migration-required rather than starting a paid rebuild. Summary one-shot `provider`/`model` options change generated summary text only, the stored vector still uses the configured embedding route.  

## Embedding Index And Local Model Commands

All `/embedding_index` actions are bot-owner-only (not delegated/native chat-admin operations). Scope defaults to `this_topic`, metadata reads/preview do not call providers, starting/running jobs is an explicit potentially paid action.  

- `/embedding_index status [scope=...] [id=N]`: list current index/coverage/job metadata or inspect one job
- `/embedding_index preview profile=NAME [scope=...] [model=MODEL]`: report current coverage, eligible source counts and expected bounded batches
- `/embedding_index start profile=NAME [scope=...] [model=MODEL]`: queue an explicit side-by-side build
- `/embedding_index run id=N [batches=1..10]`: execute bounded batches, default one
- `/embedding_index resume id=N version=N` and `/embedding_index cancel id=N version=N`: versioned job control
- `/embedding_index activate id=N version=N confirm='ACTIVATE N'`: validate complete current coverage and explicitly switch the route

Local CLI equivalents use the same service, CLI status/preview/start default to scope `all` (supply a concrete `--scope` for a chat/topic). Back up SQLite before the first production migration and follow [operations.md](operations.md#embedding-index-migrations).  

- `chatterlore embedding-index status [--scope all] [--job-id N]`
- `chatterlore embedding-index preview --profile NAME [--scope all] [--model MODEL]`
- `chatterlore embedding-index adopt-preview --manifest PATH`: validate a reviewed original-provenance manifest for at most 100 explicit legacy vectors, without inference or mutation
- `chatterlore embedding-index adopt --manifest PATH --preview-hash HASH --confirmation "ADOPT HASH" --attest-provenance`: atomically adopt only the exact previewed and attested mappings, does not switch routes or overwrite verified vectors
- `chatterlore embedding-index start --profile NAME [--scope all] [--model MODEL]`
- `chatterlore embedding-index run JOB_ID [--max-batches 1]`
- `chatterlore embedding-index resume JOB_ID --version N`
- `chatterlore embedding-index cancel JOB_ID --version N`
- `chatterlore embedding-index activate JOB_ID --version N --confirmation "ACTIVATE JOB_ID"`
- `chatterlore local-model prepare --profile NAME --mode chat|embedding [--allow-download]`: explicitly prepare cached artifacts offline, downloads additionally require the profile to allow them and fit deployment limits

## CLI Reference

- `chatterlore migrate`: creates or upgrades the configured database schema through Alembic. This mutates the database, back up operator data and follow the migration guidance in [operations.md](operations.md) before running it against persistent state.
- `chatterlore polling`: clears any configured webhook and starts the long-running Telegram polling runtime. Complete [setup.md](setup.md) first and use [deployment.md](deployment.md) for runtime guidance.
- `chatterlore webhook`: registers the configured webhook and starts the long-running FastAPI webhook runtime. Configure and validate the webhook URL and secret as documented in [deployment.md](deployment.md).
- `chatterlore control-plane-dev`: starts the dedicated loopback FastAPI control-plane development runtime for use with `web/pnpm dev`. It requires backend-only `CONTROL_PLANE_DEV_USER_ID`, accepts loopback HTTP without Telegram OIDC, performs no Telegram API calls while `WEBHOOK_URL` is empty and deliberately registers `setWebhook` only when a complete HTTPS webhook/token/secret configuration is present, see [setup.md](setup.md) before using connected mode.
- `chatterlore summarize-now [--scope all|<chat_id>|<chat_id>:<thread_id>]`: summarizes pending learned messages in each selected concrete scope and compacts canonical memory. This can invoke the configured AI provider and incur external cost or data transfer, review [privacy.md](privacy.md) before using an external provider.
- `chatterlore export-memory [--scope all|<chat_id>|<chat_id>:<thread_id>] [--format md|jsonl] [--out path]`: streams primary-key-ordered export chunks to a file or standard output without constructing a second complete payload. Treat either form as potentially sensitive and protect it according to [privacy.md](privacy.md).
- `chatterlore import-jsonl --file path --chat-id <id> [--thread-id <id>]`: incrementally validates and writes durable knowledge items into the selected chat/topic scope in independent 100-row checkpoints. Imports reject files over 512 MiB or 100,000 rows, a later malformed row or provider failure leaves earlier audited batches committed, and retrying skips their exact kind/content/source tuple. Review the input, target scope, provider cost and consent implications in [privacy.md](privacy.md) before importing.
- `chatterlore doctor`: checks configured provider profiles, missing OpenAI-compatible keys, missing local optional dependencies, SQLite parent write access and memory directory write access without starting Telegram.
- `chatterlore show-config`: prints non-secret runtime settings including provider profiles, default system prompt, reasoning level, retrieval candidate limit and admin-cache TTL. Credential-bearing database URLs are redacted before display.
- `chatterlore secrets status`: validates the configured master-key ring and reports configured/active IDs, encrypted credential counts, per-key reference counts and missing referenced IDs without decrypting or printing provider secrets.
- `chatterlore secrets rewrap --to <master-key-id> [--yes]`: warns that a current database backup is required, decrypts and verifies every managed credential wrapping before any update, then atomically rewraps all DEKs under the selected configured key. Without `--yes` it asks for interactive confirmation, output contains counts and key IDs only.
- `chatterlore memory-status --scope all|<chat_id>|<chat_id>:<thread_id>`: prints the same scoped message/memory/AI usage status available through `/memory_status`.
- `chatterlore import-telegram-json --file result.json --chat-id <id> [--thread-id <id>]`: streams Telegram Desktop message arrays or Bot API update-dump arrays, then embeds and commits learned raw-message memory in independent 100-row checkpoints. The same 512 MiB and 100,000-row limits apply, unsupported shapes are rejected before business writes, while a later parse/provider failure preserves earlier audited batches for idempotent retry.
- `chatterlore import-telegram --file <export-directory|result.json|messages.html> --chat-id <id> [--thread-id <id>]`: auto-detect a reviewed Telegram export, preferring `result.json` when Both formats exist. HTML-only archives use a local streaming reader, no exported JavaScript executes and no linked resources are fetched
- `chatterlore import-telegram-html --file messages.html --chat-id <id> [--thread-id <id>]`: explicitly select HTML, including validated same-directory `messages2.html` pagination. Dates retain their timezone-qualified instant and are stored in UTC, unavailable numeric Telegram user IDs remain unknown, JSON/HTML retries share logical-message duplicate detection

For a forum, use `--thread-id 1` for a General-topic export or the actual topic ID for another individual-topic export. Without `--thread-id`, the entire archive is deliberately stored in aggregate chat memory, not distributed among topics. Telegram Desktop's observed JSON/HTML formats omit reliable per-message topic IDs, reply links and service-row order do not establish safe topic ownership. Explicit source-thread metadata that contradicts the requested topic is rejected before inference or message writes. Live `/ask`, `/memory_search` and ingestion in General use topic 1, Telegram's All view is a client aggregate (messages sent there are still General messages), use the control-plane Query chat scope for explicit whole-forum retrieval. Existing unclassified NULL-thread history is not automatically moved into General.  
- `chatterlore audit-events --limit N [--event-type name] [--scope scope] [--format text|jsonl] [--out path]`: reviews or exports `bot_events`.
- `chatterlore runtime-settings [--reload] [--set key=value]...`: views, reloads or edits safe database-backed runtime settings, including the positive `retrieval_candidate_limit` brute-force work cap.
- `chatterlore db-stats`: prints SQLite file size and table row counts.
- `chatterlore db-integrity`: runs `PRAGMA integrity_check` and exits non-zero unless SQLite reports `ok`.
- `chatterlore db-vacuum`: runs SQLite `VACUUM`.
- `chatterlore db-backup --out path`: creates a SQLite online backup.

## Common workflows

In a forum topic, enable learning and store a manual fact:
```text
/allow_topic
/remember tags=project priority=5 Project Juniper planning is Tuesday at 10:30 UTC
/memory_search Project Juniper planning
/ask When is Project Juniper planning?
```
Preview a setting change, then use the returned token in the same scope:
```text
/settings top_k=4
/settings confirm=<returned-token>
```
For cross-chat retrieval, a bot owner defines source scopes and grants one destination:
```text
/memory_profile_set team sources=-1001111111111,-1002222222222
/memory_profile_allow team scope=-1003333333333 enabled=on
```
With learning enabled on those sources, participants in destination `-1003333333333` can use `/ask profile=team <question>`. This does not copy source memory into the destination or grant provider-admin rights.  
To forget one topic, request a challenge and repeat the same target with the returned token:
```text
/forget scope=this_topic
/forget scope=this_topic confirm=<returned-token>
```
Forgetting removes application memory, Telegram history, backups and prior exports have separate lifecycles.  
Get the exact local CLI options with `uv run chatterlore --help`, `uv run chatterlore <command> --help` or `uv run chatterlore embedding-index --help`. See [Operations](operations.md) before imports, retention, upgrades or backups, several CLI commands automatically upgrade the configured schema before working.
