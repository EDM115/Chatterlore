# Secrets Handling

PZP AI expects secrets to come from environment variables, untracked env files or platform secret stores. Secrets should never be committed, baked into images or passed through runtime settings.

## Storage

- Keep `.env`, SQLite databases, generated memory files and provider keys out of git.
- Store `TELEGRAM_BOT_TOKEN`, `WEBHOOK_SECRET`, `TELEGRAM_OIDC_CLIENT_SECRET`, control-plane master-key material, OpenAI-compatible API keys and local deployment secrets in environment variables or deployment secret stores. `WEBHOOK_SECRET` must be non-empty and must not use common placeholders such as `change-me`, `changeme`, `placeholder`, `default` or `secret`.
- `AI_PROVIDER_PROFILES` should contain profile names, provider types and model names. Credentials stay in environment variables such as provider-specific API key variables.
- Several bootstrap profiles may reference separate API-key environment variables, and database-managed profiles can override the same profile's non-secret provider/model configuration without a restart. The environment key is synchronized into that managed profile as immutable, non-revealable pool metadata and participates with database-managed keys in sticky bounded rotation. A profile can contain at most ten total environment/database credential sources.
- Control-plane OIDC transaction state, session cookies and CSRF values are random opaque credentials. SQLite stores only SHA-256 state/session/CSRF hashes; PKCE verifiers are AES-GCM encrypted with an auth-specific key derived from the OIDC bootstrap secret and bound to the fixed callback. Raw cookie, state, CSRF and PKCE values must not be logged or placed in audit payloads.
- `CONTROL_PLANE_MASTER_KEYS` is a deployment-supplied JSON object whose values are base64-encoded 32-byte master keys, and `CONTROL_PLANE_ACTIVE_MASTER_KEY_ID` selects the key used for new credentials. Enabled control-plane startup fails closed on malformed JSON, missing active IDs, invalid base64 or the wrong decoded length, while `show-config` and `pzp-bot secrets status` expose IDs and counts only.
- Database-managed provider secrets use envelope encryption: every secret receives a random 256-bit data-encryption key, AES-GCM protects the secret and separately wraps that data key under an HKDF-derived master-key domain, and authenticated data binds the envelope to its profile, credential ID and safe fingerprint metadata. SQLite contains ciphertext, nonces, a wrapped data key, key ID, keyed fingerprint and last four characters—not plaintext.
- Plaintext provider credentials are decrypted only while creating the concrete client for an opaque operation lease. The managed registry cache is keyed by profile and credential revisions, contains no separately retained raw-key field and is cleared after every profile/credential mutation.
- Bot-admin profile/credential APIs never return a managed key except from the isolated `POST .../reveal` operation. Create, reveal and delete require fresh Telegram verification bound to that exact action. Reveal responses are private/no-store, are audited before return and remain in the Nuxt component only until the 30-second timer or an earlier navigation, identity, visibility, failure or unmount boundary.
- Manual bot-admin credential tests use a fixed non-sensitive chat or embedding input, are independently rate-limited and can incur provider charges. Results expose only the model, dimensions where relevant and safe attempt metadata; raw provider errors and response bodies never enter API responses, audit rows or admin alerts.

## Rotation

- Rotate `TELEGRAM_BOT_TOKEN` through BotFather, update the runtime secret, restart polling/webhook and verify `pzp-bot doctor` plus a manual Telegram smoke test.
- Rotate `WEBHOOK_SECRET` by updating the environment, restarting webhook mode and re-registering the webhook on startup.
- Rotate `TELEGRAM_OIDC_CLIENT_SECRET` by updating BotFather and the deployment secret together, restarting the control-plane runtime and completing both browser and Mini App authentication checks. Outstanding OIDC transactions encrypted from the old secret become intentionally unusable.
- Rotate an environment-bootstrap provider key at the provider, update the deployment secret, restart the process and run `pzp-bot doctor`.
- Rotate a database-managed provider key through the bot-admin provider API/UI without restarting. Add and test the replacement, allow it to become healthy/active, then disable or delete the old key; deletion cryptographically erases its encrypted columns and invalidates cached clients.
- To rotate only the vault master key, add a newly generated base64 32-byte value under a new ID while retaining every old ID, back up SQLite, run `uv run pzp-bot secrets status`, then run `uv run pzp-bot secrets rewrap --to <new-id> --yes`. The command decrypts and verifies every existing wrapping before writing any row, commits all replacements atomically, and prints counts only. After a successful backup and status check show zero references to an old ID, remove that old key from the next deployment restart.

## Deployment Notes

- For Compose or container platforms, prefer an untracked env file or platform secret injection over baking secrets into images.
- The Compose bot service is the only application container that receives `.env` and backend secrets. Caddy receives only `CONTROL_PLANE_PUBLIC_ORIGIN`, Nuxt receives only non-secret Node listener settings, and Caddy removes `Cookie`, `Authorization` and `X-CSRF-Token` before proxying frontend requests; never add bot, OIDC, vault, provider, database or webhook secrets to the frontend build arguments or environment.
- Do not pass secrets through `/runtime_settings`; editable runtime settings intentionally reject secret-bearing keys.
- Do not paste provider keys into Telegram groups or bot DMs. Bot conversations are not an encrypted application secret store, and deleting a command message would not guarantee removal from Telegram infrastructure or every update consumer.
- Provider-health DMs contain only profile name, safe credential label/fingerprint, event type and allowlisted classification. Delivery rows and retries never contain a raw key, raw exception string or provider response body.
- When sharing logs or audit exports, review them for chat/user identifiers even though provider keys and bot tokens are not logged intentionally.
- Run `pzp-bot show-config` when troubleshooting configuration because it prints non-secret effective settings, control-plane configured-state metadata and redacted credential-bearing database URLs.
