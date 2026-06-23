# Secrets Handling

## Storage

- Keep `.env`, SQLite databases, generated memory files and provider keys out of git
- Store `TELEGRAM_BOT_TOKEN`, `WEBHOOK_SECRET`, OpenAI-compatible API keys and local deployment secrets in environment variables or deployment secret stores
- `AI_PROVIDER_PROFILES` should contain profile names, provider types and model names; credentials stay in environment variables such as provider-specific API key variables

## Rotation

- Rotate `TELEGRAM_BOT_TOKEN` through BotFather, update the runtime secret, restart polling/webhook and verify `pzp-bot doctor` plus a manual Telegram smoke test
- Rotate `WEBHOOK_SECRET` by updating the environment, restarting webhook mode and re-registering the webhook on startup
- Rotate provider keys at the provider, update environment secrets, restart the process and run `pzp-bot doctor`

## Deployment Notes

- For Compose or container platforms, prefer an untracked env file or platform secret injection over baking secrets into images
- Do not pass secrets through `/runtime_settings`; editable runtime settings intentionally reject secret-bearing keys
- When sharing logs or audit exports, review them for chat/user identifiers even though provider keys and bot tokens are not logged intentionally
