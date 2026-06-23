# Commands

- `/ask [profile=name] <question>`: answer from current chat/topic memory by default. When `profile=name` is provided, retrieval also includes explicitly permissioned, learning-enabled source scopes in that profile
- `/memory_search [profile=name] <query>`: show top retrieved memory snippets, optionally using a permissioned cross-chat profile
- `/memory_export [md|jsonl] [scope=...]`: send a memory export file with readable scope/source metadata where available. Unknown formats, scopes, or extra tokens are rejected with a usage/error message
- `/memory_settings [top_k=N] [summary_batch_size=N] [summary_model=name] [raw_retention_days=N|off] [knowledge_retention_days=N|off] [review_before_compact=on|off]`: view or edit current-scope memory quality settings
- `/memory_prune [scope=...]`: apply configured raw-message and knowledge retention for a scope
- `/memory_reviews [scope=...]`: list pending review proposals for scopes with review-before-compact enabled
- `/memory_approve <review_id>`: approve a pending proposal and insert it as durable memory
- `/memory_reject <review_id>`: reject a pending proposal
- `/stats`: show message, knowledge, embedding, and event counts
- `/learn_on`, `/learn_off`, `/learn_status`: control learning for the current scope. Status also shows effective memory settings
- `/allow_chat`, `/deny_chat`: enable or disable the whole chat scope
- `/allow_topic`, `/deny_topic`: enable or disable the current topic scope
- `/allow_user <id>` or reply with `/allow_user`: allow a user for this scope
- `/deny_user <id>` or reply with `/deny_user`: deny a user for this scope
- `/allow_admins on|off`: allow chat admins as learnable authors in this scope
- `/remember [tags=a,b] [priority=N] [source=url] <text>`: add a durable manual note with optional tags, priority and source metadata
- `/memory_notes [scope=...] [tag=name]`: list manual notes for a scope, optionally filtered by tag
- `/memory_note_edit <id> [tags=a,b] [priority=N|off] [source=url|off] [text]`: edit a manual note metadata and optional text
- `/memory_note_delete <id>`: delete a manual note from the current scope
- `/memory_profiles`: list named cross-chat memory profiles
- `/memory_profile_set <name> sources=<scope,scope>`: create or replace a profile source scopes. Source scopes use the same scope grammar as exports
- `/memory_profile_allow <name> [scope=...] [enabled=on|off] [learn=on|off]`: allow or revoke a destination scope permission to query a profile source scopes. Cross-chat retrieval also requires each source scope learning policy to be enabled
- `/memory_templates`: list built-in and custom summary templates
- `/memory_template_set <name> <prompt>`: create or update a reusable custom summary template
- `/summarize_now [template=name]`: summarize pending learned messages for this scope with the general template or a named template
- `/summarize_range start=<message_id> end=<message_id> [template=name]`: summarize learned messages between two boundary messages in the current scope and record the source message IDs, date interval and template name
- `/forget scope=this_topic|this_chat|all confirm=<token>`: delete memory after a confirmation round trip
- `/help`: show command help inside Telegram

Configured `BOT_ADMINS` can administer every scope. Telegram chat admins can administer their chat. Only configured bot admins can export or forget `scope=all`. Polling and webhook startup register the current command list with Telegram through `set_my_commands`.
