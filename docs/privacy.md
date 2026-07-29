# Privacy And Data Handling

PZP AI is built around explicit learning consent, scoped retrieval and reviewable operational metadata. The defaults are conservative: the bot does not learn ordinary messages until learning is enabled for the relevant chat or topic.

## Learning And Retention

- Learning is disabled by default unless `LEARNING_DEFAULT_ENABLED=true`.
- Use `/allow_chat` and `/allow_topic` explicitly in the places where learning is desired.
- SQLite is authoritative for raw messages and committed durable knowledge. Markdown daily and canonical files are rebuildable projections of committed knowledge items, not independent content stores or full chat dumps.
- Sensitive scopes can enable review-before-compact. Pending and rejected proposals remain SQLite-only; only approval creates a durable knowledge item that may appear in Markdown.
- Durable mutations rebuild the daily projection after commit, and startup reconciles stale generated files without contacting an AI provider. Rejection, note or item deletion, knowledge retention and forget therefore remove affected projected content.
- `/forget` deletes raw messages, durable knowledge and embeddings for the selected scope after confirmation and reconciles both daily and canonical Markdown. `bot_events` audit rows remain so destructive operations can still be reviewed.
- A summary provider exception or empty response leaves every source message pending for retry. The bot never substitutes raw chat as a durable summary and creates no knowledge item, review proposal or Markdown content for the failed attempt.
- Summary claims contain only a random ownership token and claim timestamp alongside the already stored source rows. A failed provider call releases its claim; an abandoned claim can be recovered after 900 seconds, and neither path turns source chat into durable knowledge. Stale-recovery and stale-result audit events are content-free.

## Scope Boundaries

- Cross-chat memory is disabled by default.
- Named profiles do not share memory unless the destination scope is explicitly allowed to query each source scope, and source scopes must still have learning enabled.
- Review approval and rejection authorize the review's persisted owning scope: Telegram chat admins can decide reviews only for their current chat, while configured bot admins retain cross-scope access.
- `/memory_debug` explains skip reasons by message id and policy reason only; it does not return the skipped message body.
- `/ask` may append compact source references such as `<scope>#<message_id>` when the generated answer does not already cite sources. These are identifiers for auditability, not raw message dumps.

## Imports And Exports

- Manual notes can include tags, priority and source metadata; exports include that metadata alongside source message IDs and links when available.
- Telegram and CLI exports are streamed in bounded chunks rather than duplicated as one in-memory payload. Streaming lowers local memory pressure but does not make the output less sensitive; every document part and redirected standard-output file must receive the same access controls as a complete export.
- `pzp-bot import-jsonl` and `pzp-bot import-telegram-json` read local files incrementally, cap them at 512 MiB and 100,000 rows, and send at most 100 texts per embedding-provider request. This is an operator-controlled local import path, not Telegram API historical backfill, and an external embeddings profile still transfers each accepted batch to that provider and may incur cost.
- Bulk imports commit each successful 100-row batch and record content-free checkpoint counts. A later parse, validation, database or provider failure leaves prior checkpoints committed; retrying the same input skips exact JSONL kind/content/source tuples and logical Telegram message IDs instead of duplicating them.
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
- A failed post-commit Markdown rebuild records `markdown_projection_failed` with only the exception type. The committed SQLite knowledge remains authoritative, and no memory content is copied into the event.
- A failed summary records scoped `summary_failed` metadata and an AI-usage error using only the exception type, provider/model selection and source message IDs. Provider exception text, prompts and raw message bodies are excluded.
- Bulk-import checkpoint, completion and failure events contain counts, batch numbers, the local file name and exception type but no imported text or raw provider exception message.
- Admin cache entries contain Telegram user IDs for chat administrators and expire according to `ADMIN_CACHE_TTL_SECONDS`. They are process-local and are not written to SQLite or Markdown memory.
