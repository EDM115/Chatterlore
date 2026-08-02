# Operations Runbook

This runbook covers day-to-day operation for a PZP AI instance: logs, audits, runtime settings, database maintenance and admin memory controls.

## Structured Logs

- Application startup calls `configure_logging(settings)` and configures `structlog` JSON logs with ISO timestamps and levels.
- Telegram update middleware records `telegram_update` with safe chat, thread, user, message, event type, duration and error metadata.
- Telegram send failures record `telegram_rate_limited` or `telegram_api_error` with method/chat/thread/message metadata and without response payload text or secrets.

## Background Task Supervision

- Polling and webhook runtimes supervise two critical loops named `pzp-idle-summarizer` and `pzp-runtime-settings-refresher`. Control-plane-enabled runtimes also supervise the non-critical `pzp-admin-alert-delivery` loop. An ordinary iteration exception records `background_task_iteration_failed`, waits with deterministic exponential backoff capped by the loop's normal interval, and retries instead of permanently stopping the loop.
- Failure logs include only the task name, exception type, consecutive failure count and backoff seconds. They intentionally exclude exception messages, prompts, chat content and secrets.
- A successful iteration resets the consecutive failure count. Repeated failures should prompt operators to inspect database/provider availability for the idle summarizer or SQLite/runtime-setting validity for the settings refresher.
- Cancellation exits immediately during shutdown. An already-dead task is drained and emits content-free `background_task_stopped_unexpectedly` metadata so its exception is not left unobserved and shutdown can continue for the other task and Telegram session.
- Webhook `GET /health` reports the two critical task names and returns HTTP 200 only while both handles are running and not done; it returns HTTP 503 as soon as either critical task is dead. Admin-alert delivery is intentionally excluded because Telegram DM failure must never take down provider calls or production health. Polling has no HTTP server, so supervise the process and structured events externally.

## Audit Review

- Telegram admins can use `/audit_events limit=N event_type=name scope=this_topic|this_chat` for current-scope review.
- Configured bot admins can use `/audit_events scope=all` for global review.
- CLI review uses `pzp-bot audit-events --limit 100 --format jsonl --out audit.jsonl`.

Audit events are operational metadata, not memory exports. Treat them as sensitive because they still include scope and user identifiers.

`ai_usage` writes normalize scope into dedicated chat/topic columns while retaining the controlled metadata scope for backward-compatible audit output. Status queries aggregate counts, errors, average latency and per-operation totals in SQL and apply the normal scope hierarchy: global includes all telemetry, a chat includes its main thread and topics, and a topic is exact. Missing or invalid pre-revision-0012 legacy scope metadata remains unscoped and is visible only in global totals. The usage scope/time, operation/time and error/time indexes and the event type/scope/time and scope/time indexes keep append-only audit queries bounded without introducing prompt or message content into query keys.

## Provider Vault Rotation

- Run `uv run pzp-bot secrets status` before and after any master-key change. The command reports configured key IDs, the active ID, encrypted-row counts by referenced ID and any missing referenced IDs; it never reveals or decrypts a provider credential for display.
- Add the new 32-byte base64 master key while retaining every referenced old key, restart with the expanded key map, create and verify a current database backup, then run `uv run pzp-bot secrets rewrap --to <new-id> --yes`. Rewrap computes and decrypt-verifies every replacement before issuing database updates and commits the complete batch in one transaction, so an interruption or invalid row leaves all prior wrappings authoritative.
- Keep old key material available until status reports no credential references to that ID and the post-rotation provider smoke test succeeds. Removing a still-referenced ID makes those credentials undecryptable and provider refresh fails closed rather than silently falling back.
- Managed credential CRUD and reveal events live under `control_plane.provider.*` with content-free metadata. A reveal event is committed before the response leaves the server; investigate unexpected reveal frequency without exporting response bodies or browser storage.

## Provider Pool And Alert Recovery

- Bot admins can inspect `/providers` in the control plane for each managed profile's active key, eligible/cooldown/disabled counts, health epoch, masked per-key counters/timestamps and recent sanitized alerts. Ordinary page loading is read-only and never invokes a provider or downloads a local model.
- Chat and embedding checks are explicit constant-input actions, use the selected key rather than silently traversing the whole pool and may incur provider charges. The endpoint is independently rate-limited; a classified failure updates that key's health through the same safe state machine used by production calls.
- Authentication, permission and allowlisted exhausted-quota failures hard-disable the key and rotate production calls. Other rate limits, exhausted transport retries, server failures, HTTP 408 and HTTP 409 place it in bounded cooldown before rotation. Invalid requests/model payloads and unknown exceptions leave key health unchanged.
- Expired cooldown keys become eligible automatically and a later successful provider call marks the key healthy, persists it as active and emits one deduplicated recovery intent. A bot admin can also re-enable a disabled key after correcting it; re-enable clears automatic cooldown/error health so the key is eligible for a fresh test.
- `admin_alerts` keeps both transition intents and per-current-admin deliveries. `pending`/`retry` rows are delivered by the background worker, Telegram retry-after is capped at one hour, `unreachable` recipients require an admin/account or bot-permission correction, and the control plane can manually queue an undelivered row again.
- Pool exhaustion returns one controlled service error after one bounded traversal of at most ten eligible credentials. Check the pool summary and alert backlog, correct or add a credential, run one explicit test, then confirm normal traffic makes a healthy key sticky again.

## Control-Plane Diagnostics

- `GET /api/control/v1/operations/health` is a bot-admin-only cached-state diagnostic. It reports the available manual checks plus bounded overview/runtime/provider/local/alert state and never invokes an AI provider, performs an integrity scan, rebuilds Markdown or loads/downloads a local model.
- `POST /api/control/v1/operations/checks/db-integrity` runs SQLite `PRAGMA integrity_check`; `projection-status` performs a bounded read-only projection scan; `provider-profile` sends one constant safe chat or embedding input through the selected profile and therefore may consume quota; `local-model-readiness` reports dependency/load state and attempts a model load/download only when the caller explicitly sets `allow_download=true`.
- Every manual check requires current bot-admin authority plus CSRF, returns only allowlisted result/detail fields and writes a content-free `control_plane.operations.check` audit event. Provider response content, prompts, exception messages and local text are never returned or audited.
- `GET /api/control/v1/audit/events` and `/audit/ai-usage` use descending keyset cursors with a maximum 100-row page. Event payloads recursively redact sensitive keys and bound nesting, strings, mappings and lists; AI usage excludes metadata and exposes only safe provider/model/operation/token-count/latency/error-class fields. Existing `/alerts` pagination remains the authoritative delivery-state view.

## Markdown Projection Recovery

- Treat SQLite `knowledge_items` as authoritative. Files under `data/memory/daily/` and the canonical `data/memory/MEMORY.md` are generated views, so do not repair missing or private content by editing generated files as if they were the source of truth.
- Normal durable mutations rebuild the daily projection after their database transaction commits. Application startup also reconciles daily files from committed rows without an AI provider call, removing stale generated content left by an interrupted or older runtime.
- `canonical_scope_snapshots` is a derived per-scope cache, not a second memory authority. Routine mutations refresh only their dirty scopes; a matching fingerprint reuses the last committed canonical entries with no provider call. The fingerprint includes every item and provider/prompt setting that affects output, while storing content hashes instead of raw message text.
- `canonical_snapshot_refresh_failed` retains the last valid snapshot and leaves the current item fingerprint unmatched so the next compaction retries. `canonical_snapshot_stale_result` means knowledge or output settings changed during provider work and the old result was discarded; `canonical_snapshot_retry_exhausted` means repeated concurrent drift prevented a stable commit. Invalid snapshot JSON is treated as missing and recomputed rather than silently omitted from `MEMORY.md`.
- For an explicit full repair after backing up SQLite and confirming provider cost/privacy, run `uv run python -c "import asyncio; from pzp_ai_bot.app_context import build_context; from pzp_ai_bot.config import get_settings; context=asyncio.run(build_context(get_settings())); print(asyncio.run(context.memory.compact_memory(force=True)))"`. This recomputes every non-empty scope; a no-argument compaction reuses matching snapshots and only repairs missing, corrupt or fingerprint-mismatched scopes.
- If `markdown_projection_failed` appears in `bot_events`, check `MEMORY_DIR` permissions, free disk space and unexpected links or paths beneath its `daily` tree. The database change has already committed; fix the filesystem condition and restart the application to reconcile the projection.
- Forget and knowledge-retention operations remove matching content from daily and canonical Markdown while leaving content-free `bot_events` metadata available for audit.

## Summary Failure Recovery

- `summary_failed` means the provider raised or returned an empty response. The source messages remain unsummarized and eligible for the next idle, automatic or manual summary attempt; the failed attempt creates no knowledge item, review proposal or Markdown content.
- Review the event's scope, source message IDs, provider, model and exception type together with the matching `ai_usage` row. Diagnostics intentionally exclude raw chat, prompts and provider exception strings because those may contain sensitive request data.
- Provider failure telemetry is committed independently only after the failed business mutation has rolled back or a summary claim has been released. Its `ai_usage.error` value is a fixed bounded summary, while `metadata_json` contains controlled metadata only—such as operation, scope, profile, model, count/dimensions, prompt/template version and exception type—and never prompt text, message content, raw exception bodies, response bodies, keys or credential-bearing URLs.
- Correct the provider credential, availability or response issue, then run `pzp-bot summarize-now --scope <scope>` or allow the normal summarizer to retry. A zero result means no message batch completed.
- A worker claims at most the effective `summary_batch_size` rows for 900 seconds. Provider failure releases the claim immediately; a vanished worker's older claim becomes eligible for atomic recovery by the next automatic, idle or manual claim attempt.
- `summary_stale_claims_recovered` reports only the recovered claim count and timeout. Occasional recovery after a forced shutdown is expected; repeated recovery means provider latency or worker termination should be investigated. Fresh claims remain unavailable to other workers until finalized, released or stale.
- Manual `summarize-now` drains eligible work in bounded provider requests. Ingestion performs at most one batch per update, and an idle tick performs at most one batch per eligible scope so backlog in one scope cannot starve others.

## Runtime Settings

- Safe runtime defaults live in `runtime_settings` and are validated before write and again before refresh.
- Polling and webhook runtimes run a background refresher using `RUNTIME_SETTINGS_REFRESH_SECONDS`, so safe setting changes take effect without restarting the process.
- `pzp-bot runtime-settings --set retrieval_top_k=8 --set answer_min_score=0.2` edits safe defaults and writes a `runtime_settings_update` audit event.
- `/runtime_settings reload=on` forces a refresh from Telegram and is restricted to configured bot admins because runtime defaults are global.

Secrets are not runtime settings. Keep provider keys, Telegram tokens and webhook secrets in environment variables or deployment secret stores.

## Control-Plane Owners And Managed Chats

- `BOT_ADMINS` is the immutable recovery-owner set. Database-managed bot owners take effect without restart; removing one requires fresh exact-action Telegram verification, revokes that user's live sessions and refuses to remove a bootstrap owner or the final effective owner.
- Delegated chat-admin grants require an active managed chat and a successful Telegram `getChatMember` check proving the target is a non-bot member. Telegram-native admins and delegated admins receive only that active chat's settings/admin surface; a foreign, paused or detached chat is denied server-side even if the browser still displays stale navigation.
- Enroll groups from `/chats`. A supported Mini App uses a short-lived prepared selection; a regular browser or older Telegram client receives the requester-bound button in the bot DM. The bot accepts the resulting `chat_shared` update only in that requester's private bot chat, then rechecks the requester's admin status, the group type and bot membership before activation.
- Pause stops ingestion and idle summarization without deleting memory. Resume reactivates them. Detach retains memory and the bot's Telegram membership but removes the chat from scoped-admin operation. Leave calls Telegram and then detaches. Forget is independent of lifecycle state and deletes the selected chat's messages, knowledge and embeddings only after bot-owner step-up, exact confirmation text and current-version validation; Markdown projections are reconciled afterward.
- Bot Settings edits only the safe runtime allowlist. Chats shows effective, inherited and exact override values separately for main-chat/topic scopes so an operator can distinguish a local override from a global or chat-level value.

## Retrieval Capacity

- `retrieval_candidate_limit` defaults to 5,000 candidates per authorized source scope. Durable knowledge embeddings consume the quota first; only the remaining slots are filled by the most recent learned-message embeddings, so recent raw chat cannot crowd out an older durable fact.
- A deterministic 2026-07-29 SQLite benchmark measured the old N+1 path at 1,001 queries and 2.128396 seconds for 1,000 candidates, then 10,001 queries and 15.226532 seconds for 10,000 candidates. The bounded batch path used four retrieval queries at both sizes and measured 0.257670 seconds for 1,000 candidates and 0.512913 seconds while selecting 5,000 of 10,000 candidates. Wall-clock values are local observations, not CI thresholds; the regression gate asserts ordering, candidate count and constant query count.
- Inspect the current value with `uv run pzp-bot runtime-settings` or `/runtime_settings`. Tune it without restart through `uv run pzp-bot runtime-settings --set retrieval_candidate_limit=5000` or `/runtime_settings retrieval_candidate_limit=5000`; values must be positive.
- `retrieval_top_k` controls returned results and is separate from the candidate-work cap. Named profiles apply the cap once per authorized source scope, so broad profiles multiply retrieval work.
- Move to a native vector index when representative recall requires candidates outside the deterministic durable-first/recent-message window, measured latency remains unacceptable at the safe cap, or corpus growth makes per-scope brute-force scans operationally expensive.

## Database Maintenance

- `pzp-bot db-stats` prints SQLite file size and table row counts.
- `pzp-bot db-integrity` runs `PRAGMA integrity_check` and should report `integrity_check: ok` before releases or backups.
- `pzp-bot db-backup --out data/backups/pzp.sqlite` creates an online backup without copying a hot database file directly.
- `pzp-bot db-vacuum` can reclaim SQLite space after large forget/prune operations; run it during a quiet maintenance window.

## Admin Memory Operations

- Use `/memory_status scope=this_topic` or `pzp-bot memory-status --scope <scope>` to inspect per-scope learned, skipped, knowledge and AI usage counts before changing policy.
- Use `/memory_scopes scope=this_chat` to discover known forum topics and their effective learning state before allowing or denying a topic.
- Use `/memory_debug limit=10` to review why recent updates were skipped. The response is intentionally metadata-only and does not echo skipped message bodies.
- Use `/memory_items`, `/memory_item_delete` and `/memory_item_refresh` for item-level memory maintenance when a single bad summary or stale embedding should be fixed without forgetting an entire chat/topic.
- Use `/settings key=value` to preview broad scoped settings changes, then `/settings confirm=<token>` to apply. The direct `/memory_settings` command remains available for trusted local dogfooding and scripted admin work.
- Use `pzp-bot import-jsonl` or `pzp-bot import-telegram-json` only after confirming the target scope, the 512 MiB file limit, the 100,000-row limit and the cost/privacy characteristics of the selected embedding provider. Parsing is incremental through line iteration or the pinned `ijson==3.5.1`, embeddings are requested in independent batches of at most 100 texts outside database transactions, and each short write transaction records a content-free checkpoint.
- Bulk-import atomicity is per batch, not per file. If a later row, parser, provider or write fails, inspect `jsonl_import_batch`, `telegram_json_import_batch` and the corresponding `*_failed` event, correct the input/provider problem, then retry the original command; committed JSONL tuples and Telegram logical message IDs are skipped idempotently.
- CLI exports write bounded chunks directly to the selected file or standard output. Telegram exports aggregate the same stream into UTF-8-safe deterministic parts below 45 MiB and send each part before producing the next, so operators should retain or delete every part as one sensitive export set.
- Control-plane item and review lists are keyset-paginated and scope-authorized. Item detail contains durable knowledge only; raw learned-message bodies require the separate audited `/api/control/v1/memory/items/{id}/sources` POST, are limited to 100 exact sources and should be opened only when operationally necessary.
- Browser Markdown/JSONL exports stream the same existing generator with stable scope filenames, private/no-store headers and disconnect cleanup. Treat downloaded files as sensitive plaintext and store or delete them according to the same policy as CLI and Telegram exports.
- Use `pzp-bot import-telegram-json --file result.json --chat-id <id> [--thread-id <id>]` for local Telegram Desktop exports or Bot API update dumps. Unsupported JSON shapes are rejected before business writes; after a successful or resumed import, run `/memory_status` to confirm imported/skipped counts.

## Admin Cache

- `ADMIN_CACHE_TTL_SECONDS` controls how long Telegram chat administrator lookups are cached in memory. Set it to `0` for near-immediate refresh during debugging, or keep the default `300` seconds for normal operation.
- `AdminService.cache_stats()` exposes native-cache entry/expiry counts, TTL, effective bootstrap/managed bot-owner count, delegated-grant count and active managed-chat count for tests and future diagnostics. `pzp-bot show-config` prints the configured TTL without exposing admin IDs and redacts credentials embedded in database URLs.
