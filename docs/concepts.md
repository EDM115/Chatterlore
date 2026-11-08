# Concepts

Chatterlore retrieves stored conversation context before asking a model to answer. It does not train or fine-tune the model on your group. An embedding is a numerical representation used to find related stored text, a summary is generated text stored as durable knowledge.

## Scopes

Every message and memory item belongs to a chat and optionally a forum topic. Queries and management commands use the current chat/topic unless you select another authorized scope.

| Scope | Meaning |
| --- | --- |
| `current` or omitted in Telegram | The chat/topic where you sent the command |
| `this_topic` | The current topic, or the current non-topic chat |
| `this_chat` or a numeric chat ID | The whole chat, including all its topics |
| `<chat_id>:<thread_id>` | One topic in one chat |
| `<chat_id>:main` | A chat scope with no topic restriction, also accepts `none` or an empty thread component |
| `all` | Every chat/topic, reserved for authorized global operations |

For example, `-1001234567890:42` selects topic 42, `-1001234567890` selects the whole forum. Telegram's General topic is topic 1. Its All view is a client-side aggregate, it does not create another scope. A private chat with the bot has its own scope and does not inherit group memory. A bot admin querying a group from a DM must use a command that accepts an explicit scope, an authorized memory profile or the control-plane Query page.

A topic query cannot read sibling topics. A whole-chat query can read them. Global commands do not merge their underlying storage into one conversation. The control-plane Query page requires a concrete chat/topic scope, its provider catalog does not accept `all`. CLI commands do not have a current Telegram chat, use numeric scopes or `all` as documented for that command.

## From message to answer

1. Telegram delivers a new text message or caption. The bot checks managed-chat status, learning policy and author eligibility. Duplicate logical message IDs do not create a second stored message.
2. For an accepted message, the embedding provider generates a vector and SQLite stores the source text, provenance and vector. Skipped delivered messages can still be stored as raw rows with a reason, they do not enter learned retrieval or summaries. Inactive managed chats are rejected before this persistence step.
3. Pending learned messages trigger a summary when the scope reaches `SUMMARY_BATCH_SIZE` (default 25), or after `SUMMARY_IDLE_SECONDS` of inactivity (default 900). `/summarize_now` requests a summary without waiting for those triggers.
4. A successful summary becomes durable knowledge, or a pending proposal if review-before-compact is enabled. Approval commits durable knowledge, rejection leaves the proposal outside durable memory. Failed or empty summaries leave their source messages pending for retry.
5. The bot rebuilds daily and canonical Markdown from committed knowledge. These files are readable projections, editing them does not update SQLite or teach the bot.
6. `/memory_search` embeds the query and retrieves eligible text. `/ask` uses that text as model context and includes source references. Durable knowledge takes priority, learned raw messages fill remaining result slots. A low or absent match returns an insufficient-context response instead of an unsupported generated answer.

Manual `/remember` notes are durable knowledge without waiting for automatic summarization. Notes can carry tags, priority and a source URL. Summary templates change what a summary extracts, answer system prompts change how the model uses retrieved context. Generated answers and streaming previews do not become learned knowledge through the bot's own messages.

## Learning policy and retention

Learning starts disabled. A topic rule overrides a chat rule, a chat rule overrides a global rule, and the environment/runtime learning default applies when no rule exists. `/deny_chat` therefore does not erase an existing topic allow rule. Check `/learn_status` in each relevant topic.

Bot messages, commands, empty text and messages without an author are ineligible. An explicit user deny wins over an allow. Once a matching user allowlist exists, only listed users are eligible, with an optional chat-admin exception through `/allow_admins on`. Enabling that exception alone does not make learning admin-only.

Turning learning off stops new learned ingestion, it does not delete previous memory. Existing same-scope memory remains queryable. Cross-chat memory profiles also require learning to remain enabled on their source scopes. Retention is disabled by default, raw-message and durable-knowledge retention are separate settings. Pruning old raw messages can remove the source text needed for later re-summarization, even if a durable summary remains.

`/forget` deletes the selected scope's messages, knowledge and embeddings after confirmation and reconciles Markdown. It preserves audit and AI-usage records. Deleting bot memory does not delete Telegram chat history, downloaded exports or external backups.

## Two kinds of profile

| Term | Controls | Example |
| --- | --- | --- |
| Memory profile | Which explicitly granted chat/topic sources a destination may retrieve | `team` contains two project chats, only an approved destination may use `/ask profile=team ...` |
| Provider profile | Which AI endpoint, capabilities, model defaults and credential pool an operation uses | `answers`, `summaries` and `vectors` route three AI operations independently |

Creating a memory profile alone grants no cross-chat access. A bot admin must define its sources and allow the destination through `/memory_profile_allow`. A provider profile grants no memory access. In Telegram query options, `profile=` means memory profile and `provider=` means AI provider profile.

## Settings precedence

Environment variables override `.env`. Persisted safe runtime settings override environment defaults. Scoped settings then apply from global to chat to topic, with permitted one-shot command options taking precedence for that request. A profile change uses the selected profile's model defaults rather than carrying a lower-precedence model from another provider.

An unset scoped field inherits its parent. Use `inherit` in supported command options or Reset to inherited in the UI to remove an override, copying the parent's current value creates a fixed override instead. Bot Settings controls runtime defaults, Chats controls scoped overlays. The UI uses resource versions to reject stale edits from another tab rather than overwriting them.

## Roles and managed chats

| Role | Authority |
| --- | --- |
| Bootstrap bot owner (`BOT_ADMINS`) | Global administration and recovery, cannot be removed in the UI |
| Managed bot owner | Global administration, granted/revoked by a bot owner, revocation also revokes that user's control-plane sessions |
| Telegram chat admin | Commands in their own chat, and control-plane access to that active managed chat after current Telegram authority checks |
| Delegated chat admin | CHATTERLORE UI and command-admin access granted by a bot owner for one active chat, cannot manage grants or global settings |
| Ordinary participant | Regular ask/search/help use in the chat, cannot change admin settings |

Delegation grants CHATTERLORE's scoped admin checks for an active managed chat, it does not change the person's Telegram platform membership or moderation rights. Browser login uses Telegram OIDC, Mini App login uses signed Telegram launch data. Sensitive control-plane actions require fresh verification for the exact action. Browser sign-out ends its browser session, other sessions and owner grants have separate lifecycles.

When you enable the control plane, group membership alone is insufficient for ingestion. A bot owner must enroll the group in Chats and enable learning for the intended scope. Enrollment checks that the requester administers the group and the bot is a member. The requester must start the bot DM to receive a browser enrollment selection.

| Chat state/action | Effect |
| --- | --- |
| Active | Allows learning and idle summaries when policy permits, exposes scoped admin access |
| Paused | Keeps memory, stops ingestion and idle summaries, hides scoped-admin access |
| Resume | Returns a managed chat to active |
| Detach | Stops management and keeps memory, bot membership remains |
| Leave | Detaches and asks Telegram to remove the bot, keeps memory |
| Forget | Deletes application memory after separate destructive confirmation |

## Embedding spaces

Stored and query embeddings must use the same semantic configuration. Matching dimensions do not prove that two models, revisions, normalization modes or query/document prompts are compatible. An answer or summary model change is independent of this requirement.

To change embeddings on populated memory, preview the source count and cost, start a side-by-side reindex job, let bounded batches finish, then activate the completed index. Activation verifies current source coverage, cancellation does not switch the active route. Legacy vectors without reliable identity remain unverified until rebuilt from retained source text or adopted using attested original provenance. See [Operations](operations.md#embedding-index-migrations).
