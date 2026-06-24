# Privacy And Data Handling

- Learning is disabled by default unless `LEARNING_DEFAULT_ENABLED=true`
- Use `/allow_chat` and `/allow_topic` explicitly in the places where learning is desired
- Raw messages are stored in SQLite until retained or pruned by configured scope retention. Markdown memory files contain categorized summaries and manual notes, not full chat dumps
- Sensitive scopes can enable review-before-compact so proposed summaries must be approved before becoming durable memory
- Cross-chat memory is disabled by default. Named profiles do not share memory unless the destination scope is explicitly allowed to query each source scope, and source scopes must still have learning enabled
- Manual notes can include tags, priority and source metadata; exports include that metadata alongside source message IDs and links when available. Telegram exports are sent as documents and split into parts for large payloads
- Range summaries store the boundary message IDs, source message IDs, date interval and summary template name
- `REDACT_BEFORE_AI=true` masks secret-like key/value pairs, bearer/common token forms, emails, phone numbers and configured custom regex patterns before text is sent to an OpenAI-compatible provider
- `AI_PROVIDER_PROFILES` can define multiple non-secret provider profiles. Provider secrets stay in environment variables or deployment secrets; missing credentials fail closed instead of silently sending data to the wrong provider
- AI usage records provider/profile/model/reasoning/prompt metadata and embedding dimensions/counts for cost and audit visibility. It does not store provider API keys
- The fake provider is deterministic and local. The local embedding provider avoids cloud embeddings but still requires downloading a sentence-transformers model
- Telegram Bot API send failures and retry-after responses are logged with chat/thread/message/method metadata and without export payload text or provider secrets. Update processing should not crash just because an outbound reply failed
- Telegram Bot API privacy mode must be disabled, or the bot must be a group admin, for the bot to receive normal group messages
- Runtime settings that are safe to edit are stored in SQLite with the current value, previous value, updater, timestamp and version. Secrets are not accepted as runtime settings
- Forget confirmations are persisted as hashed one-time challenges with expiry and consumption timestamps. Plain confirmation tokens are only shown to the admin who requested the destructive operation
- `/audit_events` and `pzp-bot audit-events` expose admin, settings and destructive-operation event metadata from `bot_events` for review. They do not include provider API keys or Telegram bot tokens
- `/forget` deletes raw messages, durable knowledge and embeddings for the selected scope after confirmation. Audit rows remain so destructive operations can still be reviewed

## Phase 6 Product Controls

- `/memory_debug` explains skip reasons by message id and policy reason only; it does not return the skipped message body, so admins can debug policy without redistributing private text.
- `pzp-bot import-telegram-json` imports Telegram Desktop JSON exports locally into the configured SQLite database and embeddings provider. This is an operator-controlled local import path, not Telegram API historical backfill. Review the export file before importing because it may contain old group content outside the bot's live-learning consent moment.
- Telegram JSON import preserves message text plus selected metadata such as links, document file names and photo attachment labels from Telegram Desktop exports or Bot API update dumps. It does not import binary media files into the bot storage.
- `/ask` may append compact source references such as `<scope>#<message_id>` when the generated answer does not already cite sources. These are identifiers for auditability, not raw message dumps.
- `/settings` stores pending scoped setting changes with a hashed confirmation token and expires them using the configured confirmation TTL. Applied changes write `scope_settings_confirmed` audit events.
- Admin cache entries contain Telegram user IDs for chat administrators and expire according to ADMIN_CACHE_TTL_SECONDS. They are process-local and are not written to SQLite or Markdown memory.
