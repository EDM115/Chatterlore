# Setup

This guide gets a local PZP AI instance ready for development, offline verification and first Telegram testing.

## Requirements

- Python 3.12
- [uv](https://docs.astral.sh/uv/) for dependency management and command execution
- pnpm 11.18.0 for control-plane frontend development; `web/package.json` makes pnpm download and use the exact Node.js 24.18.0 runtime
- A Telegram bot token from BotFather for live Telegram use
- Docker Desktop if you want to run the container smoke checks

## Local Development

```powershell
uv sync --extra dev --extra local
Copy-Item .env.example .env
uv run pzp-bot migrate
uv run pzp-bot doctor
uv run pytest
```

The default `AI_PROVIDER=fake` is deterministic and does not call external AI services. Use it for local development, CI and documentation examples.

The control-plane frontend is developed separately and is deployed as its own Nuxt SSR Node service rather than being copied into the Python runtime:

```powershell
Set-Location web
pnpm install --frozen-lockfile
pnpm runtime:check
pnpm dev
```

The development frontend uses credentialed same-origin `/api/control/v1` requests and proxies `/api` to `PZP_API_DEV_ORIGIN`, which defaults to `http://127.0.0.1:8000`. Nuxt SSR returns public pages and a neutral protected-route gate before hydration; authentication and privileged data still load only in the browser. Phase 5 includes browser OIDC, signed Mini App exchange, server-side sessions, capability-driven navigation, bot-admin provider/credential APIs, controlled reveal, classified sticky key-pool failover and a protected Providers page for pool health, explicit constant-input checks and sanitized alert retry. The backend routes are attached when the webhook FastAPI application starts with `CONTROL_PLANE_ENABLED=true`; bot settings, chats, query, memory, statistics and broader operations forms remain unavailable until later Plan 021 phases.

For a production-style local build, run `pnpm build`, `pnpm scan:generated` and `pnpm smoke:ssr` from `web/`. The production Compose stack keeps Nuxt and FastAPI private behind Caddy, strips credential headers before frontend requests and requires webhook configuration; see [deployment.md](deployment.md) before starting it.

To configure the Phase 3 identity boundary, create a Telegram Login OIDC client in BotFather, allow the exact public origin and register the fixed callback `<CONTROL_PLANE_PUBLIC_ORIGIN>/auth/telegram/callback`. Use an HTTPS origin without a path, query or fragment:

```dotenv
CONTROL_PLANE_ENABLED=true
CONTROL_PLANE_PUBLIC_ORIGIN=https://control.example.com
TELEGRAM_OIDC_CLIENT_ID=
TELEGRAM_OIDC_CLIENT_SECRET=
CONTROL_PLANE_AUTH_MAX_AGE_SECONDS=300
CONTROL_PLANE_STEP_UP_MAX_AGE_SECONDS=300
CONTROL_PLANE_SESSION_IDLE_SECONDS=43200
CONTROL_PLANE_SESSION_ABSOLUTE_SECONDS=604800
```

Generate each vault master key independently with `uv run python -c "import base64,secrets; print(base64.b64encode(secrets.token_bytes(32)).decode())"`, place the result in the deployment secret store as a JSON value under a non-secret key ID, and select that ID with `CONTROL_PLANE_ACTIVE_MASTER_KEY_ID`. Do not paste the generated material into source control, logs, Telegram or shell history shared with other users.

Enabling the control plane fails closed when the HTTPS origin, Telegram bot token, OIDC client ID, OIDC client secret, valid master-key map or active key ID is absent. Every master-key value must be valid base64 that decodes to exactly 32 bytes. `pzp-bot show-config` reports only the origin, fixed callback, configured-state booleans, key IDs and TTL metadata; `pzp-bot secrets status` reports key-reference counts, and neither command prints OIDC, master-key or provider-secret material.

`CONTROL_PLANE_MAX_CREDENTIALS_PER_PROFILE=10` bounds the total environment plus database-managed key sources for one managed profile. `CONTROL_PLANE_PROVIDER_TEST_RATE_LIMIT=5` limits explicit paid credential checks per client in the configured control-plane rate window, while `CONTROL_PLANE_ALERT_DELIVERY_INTERVAL_SECONDS=5` controls the best-effort Telegram admin-DM worker interval. Provider tests and local-model download checks never run automatically during page load.

For an OpenAI-backed run, keep the key out of committed files and select the model plus reasoning effort explicitly:

```dotenv
AI_PROVIDER=openai_compatible
OPENAI_API_KEY=
OPENAI_BASE_URL=https://api.openai.com/v1
CHAT_MODEL=gpt-5.6-terra
REASONING_LEVEL=medium
EMBEDDING_MODEL=text-embedding-3-small
ALLOWED_CHAT_MODELS=gpt-5.4,gpt-5.4-mini,gpt-5.4-nano,gpt-5.5,gpt-5.6-luna,gpt-5.6-terra,gpt-5.6-sol
ALLOWED_EMBEDDING_MODELS=text-embedding-3-small
ALLOWED_REASONING_LEVELS=medium
```

`CHAT_MODEL` and `REASONING_LEVEL` provide environment defaults. Bot admins can replace them through the safe database-backed `chat_model` and `reasoning_level` runtime settings, per-scope `/settings`, or the documented one-shot `/ask` and `/summarize_now` options. The production allowlist above intentionally excludes Pro variants and restricts this deployment to the listed GPT-5 models at medium reasoning. For recognized OpenAI reasoning-model IDs, the OpenAI-compatible provider forwards the selected level as the Chat Completions `reasoning_effort` parameter; the prompt and usage metadata retain the same effective value.

## Telegram Preparation

1. Create a bot with BotFather and set `TELEGRAM_BOT_TOKEN` in `.env`.
2. Add the bot to the target supergroup.
3. In BotFather, run `/setprivacy`, choose the bot and select `Disable` if the bot must learn ordinary non-command group messages. Making the bot a group admin is the other practical way to receive those updates.
4. Set `BOT_ADMINS` to the Telegram user IDs that should administer every scope.
5. Start polling with `uv run pzp-bot polling` or use the Docker/Compose flow in [deployment.md](deployment.md).
6. In Telegram, run `/allow_chat` for the whole chat or `/allow_topic` inside a forum topic before expecting live learning.

Telegram does not expose arbitrary historical group backfill through the Bot API. PZP AI learns live updates after consent is enabled and can import reviewed Telegram Desktop exports or Bot API update dumps locally with `pzp-bot import-telegram-json`.

## First Verification

Run these after configuration changes:

```powershell
uv run pzp-bot doctor
uv run pzp-bot show-config
uv run pzp-bot memory-status --scope all
```

Then run `/learn_status` and `/ask <question>` in the target Telegram scope to confirm the bot sees the expected chat or topic.
