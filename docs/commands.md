# Commands

- `/ask <question>`: answer from current chat/topic memory.
- `/memory_search <query>`: show top retrieved memory snippets.
- `/memory_export [md|jsonl] [scope=...]`: send a memory export file. Unknown formats, scopes, or extra tokens are rejected with a usage/error message.
- `/stats`: show message, knowledge, embedding, and event counts.
- `/learn_on`, `/learn_off`, `/learn_status`: control learning for the current scope.
- `/allow_chat`, `/deny_chat`: enable or disable the whole chat scope.
- `/allow_topic`, `/deny_topic`: enable or disable the current topic scope.
- `/allow_user <id>` or reply with `/allow_user`: allow a user for this scope.
- `/deny_user <id>` or reply with `/deny_user`: deny a user for this scope.
- `/allow_admins on|off`: allow chat admins as learnable authors in this scope.
- `/remember <text>`: add a durable manual note.
- `/summarize_now`: summarize pending learned messages for this scope.
- `/forget scope=this_topic|this_chat|all confirm=<token>`: delete memory after a confirmation round trip.
- `/help`: show command help inside Telegram.

Configured `BOT_ADMINS` can administer every scope. Telegram chat admins can administer their chat. Only configured bot admins can export or forget `scope=all`. Polling and webhook startup register the current command list with Telegram through `set_my_commands`.
