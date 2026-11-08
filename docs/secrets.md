# Secrets

Keep Telegram, OIDC, provider and master-key secrets in environment variables, untracked environment files or a platform secret store. Restrict access to SQLite, backups, generated memory and exports, they can contain chat content and identity data. See [privacy](privacy.md) for data retention and [configuration](configuration.md) for environment names/defaults.

## Configuration and storage

`.env`, data and keys stay out of Git and container images. The Compose bot service loads `.env`, the frontend receives no backend configuration and Caddy removes cookies, authorization and CSRF headers before forwarding frontend requests. Avoid plain `docker compose config` output in shared logs because it expands secret values, use `docker compose config --quiet` for validation.  
Keep `TELEGRAM_BOT_TOKEN`, `WEBHOOK_SECRET`, `TELEGRAM_OIDC_CLIENT_SECRET`, provider keys and vault keys in deployment secret injection. Generate an independent webhook secret, startup rejects blank/common placeholders. Telegram permits letters, digits, underscores and hyphens for this value. A hexadecimal random value meets that constraint.  
`Settings` loads declared fields such as `OPENAI_API_KEY` from the root `.env`. Custom `api_key_env`/`openai_api_key_env` names in `AI_PROVIDER_PROFILES` resolve through the process environment, arbitrary entries in `.env` are not enough for local CLI runs. Export them in the shell, load them through Compose `env_file` or inject them through the hosting platform. Prefer these references over embedded JSON credentials.  
A profile inherits the default remote key only for the same configured endpoint, a foreign endpoint requires its own explicit environment reference or managed credential. A no-auth local HTTP profile bypasses credential pools and cannot receive the cloud key, Hub download credentials also stay separate. Non-secret local/profile metadata management needs no vault, encrypted credential operations do.  
Environment credentials synchronized into managed profiles remain immutable and non-revealable. Managed profiles can override non-secret bootstrap configuration without restart, a profile supports at most ten total environment/database credential sources.

## Vault keys and encrypted credentials

`CONTROL_PLANE_MASTER_KEYS` is a JSON map of key IDs to base64-encoded 32-byte values. `CONTROL_PLANE_ACTIVE_MASTER_KEY_ID` chooses the key for new credentials. Enabled control-plane startup rejects malformed/incomplete key configuration. Generate a value in PowerShell 7:

```powershell
[Convert]::ToBase64String([Security.Cryptography.RandomNumberGenerator]::GetBytes(32))
```

Place the output in the deployment secret store and clear any shared terminal capture. `show-config` and `chatterlore secrets status` report key IDs/counts without credential plaintext. Store master keys separately from database backups and retain the keys needed by those backups.  
Each managed provider credential has a random data-encryption key. AES-GCM encrypts the secret and wraps its data key under a master-key-derived domain, authenticated data binds it to the profile, credential ID and safe metadata. SQLite holds ciphertext, nonces, wrapped key, key ID, keyed fingerprint and last four characters. Managed clients use opaque operation leases and revision-aware caches, mutations invalidate cached clients.  
Create, replace, reveal and delete require fresh Telegram verification bound to the exact action. Reveal is a separate audited POST with private/no-store headers, the browser clears it within 30 seconds or earlier on navigation, identity/visibility change, failure or unmount. Replacement keeps the credential ID, rotates its encrypted material and resets health/cooldown/error/counters. Deletion erases secret-bearing columns, retained backups can still contain the old envelope.  
The loopback development command can create the ignored stable `data/control-plane-dev-master-key` when no complete keyring exists. An unreadable/malformed existing key fails closed rather than being replaced. This colocated file supports development restart continuity, supply externally managed keys in production and do not copy the development key into a deployment.

## Sessions and identity material

OIDC state and browser session bearers are random opaque credentials. SQLite stores state/session/CSRF hashes, PKCE verifiers use authenticated encryption derived from the OIDC secret and bound to the callback. CSRF values are stable per-session HMAC values keyed by the random bearer. Keep raw cookies, state, CSRF, PKCE and Mini App `initData` out of logs, audit exports and browser storage.  
Bot-admin provider tests use fixed non-sensitive inputs and can incur charges. Diagnostics, usage and admin DMs expose safe model/dimension/classification/fingerprint metadata, raw exceptions or provider responses must not enter those channels. Review identifiers before sharing logs/audits even when credential values are masked. `uv run chatterlore show-config` is the configuration troubleshooting surface, it does not validate provider authentication.  
Do not submit provider keys through Telegram messages or safe runtime settings. Telegram message deletion cannot guarantee removal from Telegram infrastructure or other update consumers. Use the provider UI/API vault flow or deployment injection.

## Rotation

| Secret | Procedure |
| --- | --- |
| Telegram token | Rotate in BotFather, update deployment secret, restart the sole polling/webhook runtime and verify real Telegram delivery |
| Webhook secret | Update deployment secret and restart webhook mode, startup re-registers it, verify missing/wrong headers fail |
| OIDC client secret | Update BotFather and deployment together, restart, complete browser/Mini App login, outstanding transactions encrypted under the old secret become unusable |
| Environment provider key | Rotate at provider, update process secret and restart, run an explicit selected-profile check |
| Managed provider key | Add/test a replacement before retiring the old entry, or use versioned Replace for the same slot, check inference and pool health without restarting |

To rotate a vault master key:

1. Add a new random 32-byte key under a new ID and retain every referenced old ID
2. Select the intended active ID, restart with the expanded keyring and create a verified database backup
3. Run `uv run chatterlore secrets status`, then `uv run chatterlore secrets rewrap --to new-id --yes`
4. Recheck status and run an explicit provider smoke, rewrap validates replacement envelopes before committing the batch atomically
5. Remove an old key from the active deployment only after its live reference count is zero, retain separate recovery material for backups that still reference it

An interrupted/invalid rewrap leaves prior wrappings authoritative. A missing referenced key makes those credentials unusable and fails closed, restoring the correct key recovers them. Follow [backup and restore](operations.md#backup-upgrade-and-restore) before changing keys or schema.
