# Operations Runbook

This runbook covers day-to-day operation for a PZP AI instance: logs, audits, runtime settings, database maintenance and admin memory controls.

## Structured Logs

- Application startup calls `configure_logging(settings)` and configures `structlog` JSON logs with ISO timestamps and levels.
- Telegram update middleware records `telegram_update` with safe chat, thread, user, message, event type, duration and error metadata.
- Telegram send failures record `telegram_rate_limited` or `telegram_api_error` with method/chat/thread/message metadata and without response payload text or secrets.

## Audit Review

- Telegram admins can use `/audit_events limit=N event_type=name scope=this_topic|this_chat` for current-scope review.
- Configured bot admins can use `/audit_events scope=all` for global review.
- CLI review uses `pzp-bot audit-events --limit 100 --format jsonl --out audit.jsonl`.

Audit events are operational metadata, not memory exports. Treat them as sensitive because they still include scope and user identifiers.

## Runtime Settings

- Safe runtime defaults live in `runtime_settings` and are validated before write and again before refresh.
- Polling and webhook runtimes run a background refresher using `RUNTIME_SETTINGS_REFRESH_SECONDS`, so safe setting changes take effect without restarting the process.
- `pzp-bot runtime-settings --set retrieval_top_k=8 --set answer_min_score=0.2` edits safe defaults and writes a `runtime_settings_update` audit event.
- `/runtime_settings reload=on` forces a refresh from Telegram.

Secrets are not runtime settings. Keep provider keys, Telegram tokens and webhook secrets in environment variables or deployment secret stores.

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
- Use `pzp-bot import-telegram-json --file result.json --chat-id <id> [--thread-id <id>]` for local Telegram Desktop exports or Bot API update dumps. Import into a deliberate scope and run `/memory_status` after import to confirm counts.

## Admin Cache

- `ADMIN_CACHE_TTL_SECONDS` controls how long Telegram chat administrator lookups are cached in memory. Set it to `0` for near-immediate refresh during debugging, or keep the default `300` seconds for normal operation.
- `AdminService.cache_stats()` exposes entry count, expired entry count, TTL and configured bot-admin count for tests and future diagnostics. `pzp-bot show-config` prints the configured TTL without exposing admin IDs.
