# Secrets Handling

PZP AI expects secrets to come from environment variables, untracked env files or platform secret stores. Secrets should never be committed, baked into images or passed through runtime settings.

## Storage

- Keep `.env`, SQLite databases, generated memory files and provider keys out of git.
- Store `TELEGRAM_BOT_TOKEN`, `WEBHOOK_SECRET`, `TELEGRAM_OIDC_CLIENT_SECRET`, control-plane master-key material, OpenAI-compatible API keys and local deployment secrets in environment variables or deployment secret stores. `WEBHOOK_SECRET` must be non-empty and must not use common placeholders such as `change-me`, `changeme`, `placeholder`, `default` or `secret`.
- `AI_PROVIDER_PROFILES` should contain profile names, provider types and model names. Credentials stay in environment variables such as provider-specific API key variables.
- Several profiles may reference separate API-key environment variables and admins can switch the active profile through safe runtime or scoped settings without restarting. Calls currently retry only the selected profile; automatic failover, key cooldown and reloadable secret references are not implemented yet.
- Control-plane OIDC transaction state, session cookies and CSRF values are random opaque credentials. SQLite stores only SHA-256 state/session/CSRF hashes; PKCE verifiers are AES-GCM encrypted with an auth-specific key derived from the OIDC bootstrap secret and bound to the fixed callback. Raw cookie, state, CSRF and PKCE values must not be logged or placed in audit payloads.
- `CONTROL_PLANE_MASTER_KEYS` is a deployment-supplied JSON key map and `CONTROL_PLANE_ACTIVE_MASTER_KEY_ID` identifies the intended active key. Phase 3 validates IDs and keeps values out of `show-config`; Phase 4 still owns master-key preflight and the provider credential vault, so configuring this map does not yet create or migrate managed provider secrets.

## Rotation

- Rotate `TELEGRAM_BOT_TOKEN` through BotFather, update the runtime secret, restart polling/webhook and verify `pzp-bot doctor` plus a manual Telegram smoke test.
- Rotate `WEBHOOK_SECRET` by updating the environment, restarting webhook mode and re-registering the webhook on startup.
- Rotate `TELEGRAM_OIDC_CLIENT_SECRET` by updating BotFather and the deployment secret together, restarting the control-plane runtime and completing both browser and Mini App authentication checks. Outstanding OIDC transactions encrypted from the old secret become intentionally unusable.
- Rotate provider keys at the provider, update environment secrets, restart the process and run `pzp-bot doctor`.

## Deployment Notes

- For Compose or container platforms, prefer an untracked env file or platform secret injection over baking secrets into images.
- Do not pass secrets through `/runtime_settings`; editable runtime settings intentionally reject secret-bearing keys.
- Do not paste provider keys into Telegram groups or bot DMs. Bot conversations are not an encrypted application secret store, and deleting a command message would not guarantee removal from Telegram infrastructure or every update consumer.
- When sharing logs or audit exports, review them for chat/user identifiers even though provider keys and bot tokens are not logged intentionally.
- Run `pzp-bot show-config` when troubleshooting configuration because it prints non-secret effective settings, control-plane configured-state metadata and redacted credential-bearing database URLs.
