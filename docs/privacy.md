# Privacy and data handling

The instance operator controls the SQLite database, Markdown memory, backups, provider configuration and bot membership. Tell group members what you store and which providers receive it before enabling learning or importing history. Telegram also retains conversation data under its own service rules.

## What the bot stores

| Data | Storage and use |
| --- | --- |
| Delivered messages | SQLite can retain original/normalized text, author/chat/topic IDs, timestamps, reply/source metadata, learned status and skip reason |
| Embeddings | SQLite vectors for eligible messages and durable knowledge, used for retrieval |
| Durable knowledge | Approved summaries and manual notes in SQLite, including source IDs, tags/priority/source metadata where applicable |
| Markdown | Daily files and canonical `MEMORY.md`, rebuildable projections of committed knowledge |
| Review proposals | SQLite-only until approval, rejected/pending proposals do not enter durable Markdown |
| Policy and settings | Learning rules, scoped/runtime overrides, previous values, actors, timestamps and versions |
| Identity and access | Telegram identity fields, managed chats, owner/delegated grants, server-side session and authentication metadata |
| Operational records | Audit events, provider usage/timing/model metadata, import/index job checkpoints and sanitized health/alert state |

Learning is disabled by default, but a skipped delivered message can still leave its text in local SQLite. The learning flag prevents its embedding, summary and retrieval use, it is not a no-storage guarantee. When managed-chat enforcement is active, inactive or unenrolled group scopes are rejected before message persistence. Handled slash commands do not pass through the general ingestion handler, the service also excludes command content from learning.

The database and Markdown files are not encrypted conversation storage. Provider-credential encryption protects managed API keys, it does not encrypt message/knowledge tables. Use host/disk encryption and restrict filesystem access where required. Redaction before AI does not rewrite or anonymize local records.

## Learning, retrieval and sharing

Use `/allow_chat` or `/allow_topic` to enable learning and check `/learn_status` in the actual destination topic. Topic policy overrides chat policy. User deny rules win over allow rules, an active allowlist limits eligible authors. [Concepts](concepts.md) explains policy inheritance and admin exceptions.

An ordinary query stays in its current chat/topic. Whole-chat scope includes all topics, an individual topic excludes siblings. Cross-chat retrieval requires a memory profile, a bot-admin destination grant and enabled source learning policy. A provider profile only selects AI infrastructure, it grants no memory access.

Turning learning off does not delete existing data or prevent same-scope retrieval of old memory. Pausing/detaching a managed chat stops new ingestion and idle summaries and removes scoped-admin visibility, it preserves memory for bot-owner operations. Leave also removes the bot from Telegram, forget is a separate destructive action.

The control-plane memory list/detail pages expose durable knowledge. Viewing raw source text requires a separate authorized request, returns at most 100 exact source messages and records an audit event with actor, scope, item and count. `/memory_debug` returns message IDs and policy reasons without skipped message bodies.

## What AI providers receive

| Operation | Content sent to the selected provider |
| --- | --- |
| Message/note embedding | Eligible message or note text |
| Query embedding | Search/ask question text |
| Summarization | Selected learned source messages and summary instructions |
| Canonical compaction | Durable memory selected for compaction and its instructions |
| Answer generation | The question, answer instructions and retrieved context |
| Reindex/import | Authoritative source text in bounded embedding batches |

Answers, summaries and embeddings can use different providers, review all three routes. Remote inference may incur charges. No-auth local servers never inherit the default remote API key, embedded/local routes do not fall back to a cloud provider. Explicit model preparation may download artifacts even if later inference stays local.

`REDACT_BEFORE_AI=true` masks common secret-like key/value pairs, bearer/token forms, email addresses, phone numbers and configured regex matches at the OpenAI-compatible boundary. Pattern matching can miss sensitive information, review the content and provider policy rather than treating it as anonymization. Embedded inference does not use this outbound-redaction boundary.

Provider usage records include provider/profile/model, requested/effective reasoning, token counts when available, timing, operation and source/prompt-template metadata. They exclude provider API keys. Managed pool health derives from exception classes, numeric status, bounded retry information and allowlisted provider codes. Safe health APIs and owner DMs exclude prompts, raw errors and response bodies.

## Imports and exports

Imports are operator-controlled local file reads, they do not establish consent for older conversations. Review the archive, target chat/topic, retention and AI provider before importing. A forum import without `--thread-id` creates aggregate chat memory, it does not infer topic membership from replies. Use explicit topic 1 for General.

Telegram JSON/HTML imports retain text and selected link, document-name and attachment-label metadata. They do not ingest binary attachments, execute exported scripts or fetch linked resources. HTML authors without numeric IDs remain unknown. JSON/HTML retry detection uses logical Telegram message IDs.

Imports are limited to 512 MiB and 100,000 rows, with independent 100-row checkpoints and embedding batches of at most 100 texts. A later failure leaves earlier batches committed, retrying skips existing logical messages or exact JSONL kind/content/source tuples. Checkpoints audit counts, local file name and safe error type without imported text.

Markdown/JSONL exports contain plaintext memory and provenance. Telegram sends large exports as bounded document parts, browser downloads use private/no-store headers and CLI output streams to a file or stdout. All parts, redirects and backups need the same access controls as the database. Cancelling a download stops future delivery, it cannot revoke bytes already received.

## Deletion and retention

Raw-message and durable-knowledge retention are independent and disabled by default. Retention removes eligible old records and their embeddings, durable changes reconcile Markdown. A source removed by raw retention may no longer be available for re-summarization even if a derived summary remains.

`/forget` requires a one-time challenge and deletes raw messages, durable knowledge and embeddings in its confirmed scope. The control-plane equivalent requires fresh action verification and exact-text confirmation. Both preserve audit and AI-usage records. Deleting one knowledge item or note removes its vectors and projected content, it does not delete separate raw source messages.

None of these operations deletes Telegram chat history, another provider's records, downloaded exports or operator backups. Apply your backup expiration policy separately. A failed Markdown rebuild records only an error type, committed SQLite remains authoritative and startup reconciles projections without AI calls.

## Authentication, credentials and diagnostics

Browser OIDC and signed Mini App launches create revocable server-side sessions. SQLite stores session/state/CSRF hashes, authentication/expiry/step-up metadata and encrypted one-time PKCE verifiers. It does not store raw session cookies, CSRF tokens, launch proofs, authorization codes or identity tokens. The frontend keeps the accepted Mini App proof in page memory for bounded step-up use and clears it with the session.

Nuxt SSR serves a neutral gate without privileged session data. The browser loads authorized data after hydration from FastAPI, Caddy strips cookie/authorization/CSRF headers before frontend requests. Protected API responses use private/no-store rules. Identity, grants and managed-chat state are rechecked for control-plane requests.

Managed provider credentials use AES-GCM envelope encryption, SQLite holds ciphertext, wrapped data keys, key IDs, keyed fingerprints, last-four values and lifecycle/health metadata. Master keys remain deployment secrets outside SQLite. An explicit audited reveal can place plaintext in the owning UI component for at most 30 seconds, copying to the OS clipboard requires a separate click. See [Secrets](secrets.md) for rotation and recovery.

Audit and health surfaces use identifiers, counts, actions, versions and bounded reason codes. They still reveal chat/user identifiers and operational activity. Ordinary stats/health loads do not call AI providers, load models or download artifacts. Explicit provider tests and operations checks can run inference and incur charges. Review logs and exports before sharing them.

Private streamed drafts go to the owning private chat, group/topic progress is visible to that conversation's existing audience. Native Stop is bound to the owning draft/chat/thread, hidden provider reasoning is not rendered. Partial generation is unfinished output, it is not committed knowledge. Cancellation usage records exclude partial answer prose.
