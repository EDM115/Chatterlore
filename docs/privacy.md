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
