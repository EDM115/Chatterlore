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

The control-plane frontend is deployed as its own Nuxt SSR Node service rather than being copied into the Python runtime. Install its frozen dependencies from `web/`:

```powershell
Set-Location web
pnpm install --frozen-lockfile
pnpm runtime:check
```

The backend always loads settings from the repository-root `.env`; Nuxt receives neither backend credentials nor the development Telegram identity. Configure the dedicated loopback development command in that root file:

```dotenv
CONTROL_PLANE_DEV_USER_ID=887758628
CONTROL_PLANE_DEV_ORIGIN=http://localhost:3000
CONTROL_PLANE_DEV_API_PORT=8000
WEBHOOK_URL=
```

`CONTROL_PLANE_DEV_USER_ID` is required only by `pzp-bot control-plane-dev` and must be a positive numeric Telegram user ID. The origin defaults to `http://localhost:3000` and must be an exact `http` or `https` loopback origin using `localhost`, `127.0.0.1` or `::1`; the API port defaults to `8000`. These values are loaded by `Settings` so the dedicated command receives them from the repository-root `.env`, but normal polling/webhook startup does not act on them and their presence does not relax production control-plane validation.

Use one of the following local modes:

- `pnpm dev` starts `uv run pzp-bot control-plane-dev` from the repository root and `pnpm dev:web` from `web/`, prefixes both process streams and shuts the sibling down if either exits. With empty `WEBHOOK_URL`, it is the recommended offline combined mode: it starts the database, critical background tasks, health and control-plane APIs, but creates no Telegram `Bot` or dispatcher, exposes no Telegram webhook route and sends no admin-alert DMs even when a bot token exists.
- `pnpm dev:web` starts only Nuxt. It is useful for frontend work with a separately managed API or mocked requests; protected pages cannot obtain a real session unless a compatible backend is reachable.
- `uv run pzp-bot control-plane-dev` starts only the loopback FastAPI development runtime when the frontend is managed separately.

Nuxt development proxies `/api/control`, `/auth`, `/telegram/webhook` and `/health` to `PZP_API_DEV_ORIGIN`, which defaults to `http://127.0.0.1:8000`. Each target preserves its mounted prefix so FastAPI receives the original path, while Nuxt's own `/api/_nuxt_icon` endpoint remains local. If `CONTROL_PLANE_DEV_API_PORT` changes, set `PZP_API_DEV_ORIGIN` to the matching backend origin before starting Nuxt. On the first exact session `401`, the development frontend posts an empty request to the development login endpoint; the backend selects the configured UID, persists a normal browser session and grants only the capabilities resolved for that identity. Sensitive actions still require an exact-action development step-up before the underlying request. No UID is accepted from browser request data.

To opt into real Telegram connectivity during combined development, configure a complete non-placeholder `TELEGRAM_BOT_TOKEN`, HTTPS `WEBHOOK_URL` and `WEBHOOK_SECRET`. Connected mode deliberately registers commands, calls `setWebhook`, exposes `/telegram/webhook` and starts best-effort bot-admin DM alert delivery. The webhook must be publicly reachable over HTTPS even though the browser origin remains loopback HTTP, and enabling it changes this bot's Telegram update delivery mode. An incomplete connected configuration fails before any Telegram call.

The protected SSR shell includes browser OIDC, signed Mini App exchange, server-side sessions, capability-driven navigation, bot-admin provider/credential APIs, controlled reveal, classified sticky key-pool failover, reloadless safe runtime settings, managed bot owners and managed-chat/delegated-admin/scoped-policy workflows. Providers, Bot Settings and Chats are available now; query, memory, statistics, audit and broader operations forms remain later Plan 021 phases.

`BOT_ADMINS` remains the immutable recovery-owner set. A bootstrap owner can add reloadable database-managed bot owners in Bot Settings; removal requires exact-action Telegram step-up, revokes that user's live control-plane sessions and cannot remove a bootstrap owner or the last effective owner. The runtime form accepts only the existing safe setting allowlist and never accepts tokens, vault keys, provider secrets or OIDC configuration.

Bot owners enroll a group from Chats. Current Telegram Mini Apps use a short-lived prepared `requestChat` selection; a normal browser or older client receives the same requester-bound selection button in the bot DM and the page polls for a bounded time. Telegram verifies that the requester administers the selected group and that the bot is still a member before the chat becomes active. This flow requires the production webhook runtime or explicitly connected development mode because offline `pnpm dev` intentionally creates no Telegram bot object and makes no Bot API call.

An active managed chat accepts ingestion and idle summarization and is visible to its Telegram-native or delegated chat admins. Paused and detached chats retain their SQLite/Markdown memory but stop new ingestion, idle summarization and scoped-admin visibility; a bot owner can resume them. Detach only stops management, leave also asks Telegram to remove the bot, and forget is a separate step-up plus exact-text destructive action that deletes the chat scope's messages, knowledge and embeddings and reconciles Markdown.

For a production-style local build, run `pnpm build`, `pnpm scan:generated`, `pnpm smoke:ssr` and then `pnpm start` from `web/`. Production output contains neither development-auth route nor UID-setting markers, never auto-logs in and does not start Python. The production Compose stack keeps Nuxt and FastAPI private behind Caddy, strips credential headers before frontend requests and requires real webhook/OIDC configuration; see [deployment.md](deployment.md) before starting it.

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
