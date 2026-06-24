# Privacy And Data Handling

PZP AI is built around explicit learning consent, scoped retrieval and reviewable operational metadata. The defaults are conservative: the bot does not learn ordinary messages until learning is enabled for the relevant chat or topic.

## Learning And Retention

- Learning is disabled by default unless `LEARNING_DEFAULT_ENABLED=true`.
- Use `/allow_chat` and `/allow_topic` explicitly in the places where learning is desired.
- Raw messages are stored in SQLite until retained or pruned by configured scope retention. Markdown memory files contain categorized summaries and manual notes, not full chat dumps.
- Sensitive scopes can enable review-before-compact so proposed summaries must be approved before becoming durable memory.
- `/forget` deletes raw messages, durable knowledge and embeddings for the selected scope after confirmation. Audit rows remain so destructive operations can still be reviewed.

## Scope Boundaries

- Cross-chat memory is disabled by default.
- Named profiles do not share memory unless the destination scope is explicitly allowed to query each source scope, and source scopes must still have learning enabled.
- `/memory_debug` explains skip reasons by message id and policy reason only; it does not return the skipped message body.
- `/ask` may append compact source references such as `<scope>#<message_id>` when the generated answer does not already cite sources. These are identifiers for auditability, not raw message dumps.

## Imports And Exports

- Manual notes can include tags, priority and source metadata; exports include that metadata alongside source message IDs and links when available.
- Telegram exports are sent as documents and split into parts for large payloads.
- `pzp-bot import-telegram-json` imports Telegram Desktop JSON exports locally into the configured SQLite database and embeddings provider. This is an operator-controlled local import path, not Telegram API historical backfill.
- Review import files before importing because they may contain old group content outside the bot's live-learning consent moment.
- Telegram JSON import preserves message text plus selected metadata such as links, document file names and photo attachment labels. It does not import binary media files into bot storage.

## AI Provider Safety

- `REDACT_BEFORE_AI=true` masks secret-like key/value pairs, bearer/common token forms, emails, phone numbers and configured custom regex patterns before text is sent to an OpenAI-compatible provider.
- `AI_PROVIDER_PROFILES` can define multiple non-secret provider profiles. Provider secrets stay in environment variables or deployment secrets; missing credentials fail closed instead of silently sending data to the wrong provider.
- AI usage records provider/profile/model/reasoning/prompt metadata and embedding dimensions/counts for cost and audit visibility. It does not store provider API keys.
- The fake provider is deterministic and local. The local embedding provider avoids cloud embeddings but still requires downloading a sentence-transformers model.

## Operational Metadata

- Telegram Bot API send failures and retry-after responses are logged with chat/thread/message/method metadata and without export payload text or provider secrets.
- Runtime settings that are safe to edit are stored in SQLite with the current value, previous value, updater, timestamp and version. Secrets are not accepted as runtime settings.
- Forget confirmations are persisted as hashed one-time challenges with expiry and consumption timestamps. Plain confirmation tokens are only shown to the admin who requested the destructive operation.
- `/audit_events` and `pzp-bot audit-events` expose admin, settings and destructive-operation event metadata from `bot_events` for review. They do not include provider API keys or Telegram bot tokens.
- Admin cache entries contain Telegram user IDs for chat administrators and expire according to `ADMIN_CACHE_TTL_SECONDS`. They are process-local and are not written to SQLite or Markdown memory.
