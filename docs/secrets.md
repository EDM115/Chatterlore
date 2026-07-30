# Secrets Handling

PZP AI expects secrets to come from environment variables, untracked env files or platform secret stores. Secrets should never be committed, baked into images or passed through runtime settings.

## Storage

- Keep `.env`, SQLite databases, generated memory files and provider keys out of git.
- Store `TELEGRAM_BOT_TOKEN`, `WEBHOOK_SECRET`, OpenAI-compatible API keys and local deployment secrets in environment variables or deployment secret stores. `WEBHOOK_SECRET` must be non-empty and must not use common placeholders such as `change-me`, `changeme`, `placeholder`, `default` or `secret`.
- `AI_PROVIDER_PROFILES` should contain profile names, provider types and model names. Credentials stay in environment variables such as provider-specific API key variables.
- Several profiles may reference separate API-key environment variables and admins can switch the active profile through safe runtime or scoped settings without restarting. Calls currently retry only the selected profile; automatic failover, key cooldown and reloadable secret references are not implemented yet.

## Rotation

- Rotate `TELEGRAM_BOT_TOKEN` through BotFather, update the runtime secret, restart polling/webhook and verify `pzp-bot doctor` plus a manual Telegram smoke test.
- Rotate `WEBHOOK_SECRET` by updating the environment, restarting webhook mode and re-registering the webhook on startup.
- Rotate provider keys at the provider, update environment secrets, restart the process and run `pzp-bot doctor`.

## Deployment Notes

- For Compose or container platforms, prefer an untracked env file or platform secret injection over baking secrets into images.
- Do not pass secrets through `/runtime_settings`; editable runtime settings intentionally reject secret-bearing keys.
- Do not paste provider keys into Telegram groups or bot DMs. Bot conversations are not an encrypted application secret store, and deleting a command message would not guarantee removal from Telegram infrastructure or every update consumer.
- When sharing logs or audit exports, review them for chat/user identifiers even though provider keys and bot tokens are not logged intentionally.
- Run `pzp-bot show-config` when troubleshooting configuration because it prints non-secret effective settings and redacts credentials embedded in database URLs.
