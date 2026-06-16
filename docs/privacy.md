# Privacy And Data Handling

- Learning is disabled by default unless `LEARNING_DEFAULT_ENABLED=true`.
- Use `/allow_chat` and `/allow_topic` explicitly in the places where learning is desired.
- Raw messages are stored in SQLite. Markdown memory files contain summaries and manual
  notes, not full chat dumps.
- `REDACT_BEFORE_AI=true` masks simple secret-like patterns before text is sent to an
  OpenAI-compatible provider.
- The fake provider is deterministic and local. The local embedding provider avoids cloud
  embeddings but still requires downloading a sentence-transformers model.
- Telegram Bot API privacy mode must be disabled, or the bot must be a group admin, for the
  bot to receive normal group messages.
