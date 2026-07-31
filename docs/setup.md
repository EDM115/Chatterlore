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

The control-plane client is developed separately and is not served as production static content by the Python runtime yet:

```powershell
Set-Location web
pnpm install --frozen-lockfile
pnpm runtime:check
pnpm dev
```

The development client uses credentialed same-origin `/api/control/v1` requests and proxies `/api` to `PZP_API_DEV_ORIGIN`, which defaults to `http://127.0.0.1:8000`. Phase 3 implements browser OIDC, signed Mini App exchange, server-side sessions, logout, Mini App step-up and capability-driven navigation. The backend auth routes are attached when the webhook FastAPI application starts with `CONTROL_PLANE_ENABLED=true`; privileged settings, provider, chat, memory and operations forms remain unavailable. A cold Nuxt development start can display the loading template while the initial client bundle compiles.

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

Enabling the control plane fails closed when the HTTPS origin, Telegram bot token, OIDC client ID or OIDC client secret is absent. `pzp-bot show-config` reports only the origin, fixed callback, configured-state booleans, key IDs and TTL metadata; it never prints the OIDC secret or master-key material. `CONTROL_PLANE_MASTER_KEYS` and `CONTROL_PLANE_ACTIVE_MASTER_KEY_ID` are parsed now so deployment configuration is stable, but provider-vault preflight and encrypted provider credentials do not become active until Plan 021 Phase 4.

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
