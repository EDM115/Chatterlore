# Operations Runbook  
## Structured Logs  
- Application startup calls `configure_logging(settings)` and configures `structlog` JSON logs with ISO timestamps and levels  
- Telegram update middleware records `telegram_update` with safe chat, thread, user, message, event type, duration and error metadata  
- Telegram send failures record `telegram_rate_limited` or `telegram_api_error` with method/chat/thread/message metadata and without response payload text or secrets  
## Audit Review  
- Telegram admins can use `/audit_events limit=N event_type=name scope=this_topic|this_chat` for current-scope review  
- Configured bot admins can use `/audit_events scope=all` for global review  
- CLI review uses `pzp-bot audit-events --limit 100 --format jsonl --out audit.jsonl`  
## Runtime Settings  
- Safe runtime defaults live in `runtime_settings` and are validated before write and again before refresh  
- Polling and webhook runtimes run a background refresher using `RUNTIME_SETTINGS_REFRESH_SECONDS`, so safe setting changes take effect without restarting the process  
- `pzp-bot runtime-settings --set retrieval_top_k=8 --set answer_min_score=0.2` edits safe defaults and writes a `runtime_settings_update` audit event  
- `/runtime_settings reload=on` forces a refresh from Telegram  
## Database Maintenance  
- `pzp-bot db-stats` prints SQLite file size and table row counts  
- `pzp-bot db-integrity` runs `PRAGMA integrity_check` and should report `integrity_check: ok` before releases or backups  
- `pzp-bot db-backup --out data/backups/pzp.sqlite` creates an online backup without copying a hot database file directly  
- `pzp-bot db-vacuum` can reclaim SQLite space after large forget/prune operations; run it during a quiet maintenance window  