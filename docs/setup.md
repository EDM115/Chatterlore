# Setup

Use this guide to install Chatterlore, connect an AI provider and verify the bot in a Telegram group. The web control plane is optional for a polling bot. Read [Concepts](concepts.md) for scopes, learning policy, roles and the difference between memory and provider profiles.

## Install the tools and project

Install Git and [uv](https://docs.astral.sh/uv/getting-started/installation/). Use PowerShell 7 for the examples below, the combined frontend/backend development launcher also requires it on Windows. You do not need a separate virtual-environment activation, `uv run` uses the project environment.

```powershell
git clone https://github.com/EDM115/Chatterlore.git
Set-Location Chatterlore
uv python install 3.12
uv sync --frozen --python 3.12 --extra dev,local
Copy-Item .env.example .env
uv run chatterlore migrate
uv run chatterlore doctor
```

Copy `.env.example` only for a new installation, keep an existing `.env` when updating. Run backend commands from the repository root so relative database paths and `.env` resolve there. Defaults create `data/bot.db` and `data/memory/`. Keep these paths on persistent writable storage.

The default fake provider needs no AI key or model download. `doctor` should report the provider and writable storage as ready, with the control plane disabled. It checks local configuration, it does not prove Telegram connectivity or successful model inference. You can run the offline checks in [Testing](testing.md) without a Telegram token.

## Create a Telegram bot and set its owners

1. Open [@BotFather](https://t.me/BotFather) in Telegram, run `/newbot` and follow its name/username prompts.
2. Put the returned token in `TELEGRAM_BOT_TOKEN` in `.env`. Keep the token private.
3. Set `BOT_ADMINS` to your numeric Telegram user ID, or a comma-separated list of recovery owners. Usernames and the bot's own ID are not user IDs. Replace the example IDs.
4. Start a private conversation with your new bot and send `/start`. This also permits later owner DMs from the bot.

If you do not know your numeric ID, retrieve only sender and chat identifiers from your new bot's pending updates. Run this before starting any poller and only while the bot has no webhook configured:

```powershell
@'
import httpx
from chatterlore.config import Settings

settings = Settings()
response = httpx.get(f"https://api.telegram.org/bot{settings.telegram_bot_token}/getUpdates", timeout=15)
payload = response.json()
if not payload.get("ok"):
    raise SystemExit("Telegram rejected getUpdates, check the token and that no webhook is active")
for update in payload["result"]:
    message = update.get("message", {})
    sender = message.get("from", {})
    chat = message.get("chat", {})
    print({"username": sender.get("username"), "user_id": sender.get("id"), "chat_id": chat.get("id"), "chat_type": chat.get("type")})
'@ | uv run python -
```

Use the `user_id` from your own `/start` message as `BOT_ADMINS`. No output means there is no pending message, send another DM and retry. Do not share raw API responses or token-bearing URLs. An existing bot with a live writer needs a coordinated handoff, see [Deployment](deployment.md#polling).

## Choose the AI provider before enabling learning

Leave `AI_PROVIDER=fake` for deterministic exploration. To obtain useful summaries and generated answers, configure a real provider before teaching or importing memory. Fake embeddings and real embeddings are different spaces, switching after data exists requires an [index migration](operations.md#embedding-index-migrations).

For one remote OpenAI-compatible endpoint, edit `.env`:

```dotenv
AI_PROVIDER=openai_compatible
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_API_KEY=replace-with-your-provider-key
CHAT_MODEL=replace-with-a-chat-model-id-your-account-can-use
EMBEDDING_MODEL=text-embedding-3-small
REASONING_LEVEL=medium
```

The chat model placeholder must be replaced. For another provider, use its base URL and supported model IDs, and ensure it provides embeddings or select a separate embedding profile. Model allowlists are optional deployment restrictions, empty `ALLOWED_CHAT_MODELS` and `ALLOWED_EMBEDDING_MODELS` impose no name restriction. A provider account still needs access and sufficient quota.

Use [Configuration](configuration.md#provider-profiles) to route answers, summaries and embeddings to different profiles. That guide also covers existing local HTTP servers and optional embedded models. HTTP servers do not require the local Python extra. Embedded inference requires `uv sync --frozen --extra dev,local` plus explicit model preparation, it does not download models during status checks.

After editing `.env`, run:

```powershell
uv run chatterlore doctor
uv run chatterlore show-config
```

`show-config` excludes credentials. Custom `api_key_env` references must exist in the process environment for local runs, adding an unknown key name only to `.env` is insufficient. The built-in `OPENAI_API_KEY` setting does load from `.env`.

## Start polling and verify a group

1. Add the bot to a group or forum. In BotFather, use `/setprivacy` and choose Disable so it can receive ordinary group messages, or make it a group administrator. If you change privacy settings on an existing membership, re-add the bot if Telegram still withholds messages.
2. Keep `CONTROL_PLANE_ENABLED=false` for this initial polling setup. `WEBHOOK_URL` can stay empty.
3. Start one bot process:

```powershell
uv run chatterlore polling
```

4. In the group, send `/help`, then `/allow_chat` for the whole group. For one forum topic, send `/allow_topic` inside that topic instead. You must be a Telegram chat admin or configured bot owner.
5. Send `/learn_status` in the same place. Confirm `learning_enabled: True` and note the returned `scope` (chat ID and topic ID). A topic override can differ from its parent chat.
6. Send an ordinary message such as `Project Juniper planning is Tuesday at 10:30 UTC. Mira coordinates the release.`
7. Send `/memory_search When is Project Juniper planning?`, then `/ask When is Project Juniper planning?`. Search should include the stored fact, a real chat provider should answer Tuesday at 10:30 UTC with a source reference. The fake provider only establishes the wiring.
8. Send `/summarize_now`, then `/memory_items` and `/memory_status` to inspect durable knowledge and usage. With review enabled, approve the proposal through `/memory_reviews` and `/memory_approve <id>` first.

Ordinary learned text can be retrieved before automatic summarization. `/remember Project Juniper planning is Tuesday at 10:30 UTC` adds a durable manual note. Use `/memory_export md` to inspect an export, protect the downloaded file as conversation data. Type slash commands even if Telegram shows no command suggestions, startup clears the default command menu.

Stop polling with Ctrl+C. Use [Operations](operations.md) for backups, retention, imports and maintenance. Local storage can contain the text of skipped delivered messages even when learning is off, see [Privacy](privacy.md).

## Run the web interface locally

Install [pnpm](https://pnpm.io/installation) using the release declared by `packageManager` in `web/package.json`. pnpm installs the project's declared Node 26 runtime during the frozen install. An existing Node installation is needed only if your chosen pnpm installation method requires it.

For an offline development instance, stop polling and set these values in the root `.env`:

```dotenv
AI_PROVIDER=fake
BOT_ADMINS=123456789
CONTROL_PLANE_ENABLED=false
CONTROL_PLANE_DEV_USER_ID=123456789
CONTROL_PLANE_DEV_ORIGIN=http://localhost:3000
CONTROL_PLANE_DEV_API_PORT=8000
WEBHOOK_URL=
```

The same positive test ID must appear in `BOT_ADMINS` to exercise bot-owner features. Use a separate `DB_URL` and `MEMORY_DIR` if you already have real data or persisted runtime/provider settings, changing environment defaults does not clear those settings.

```powershell
Set-Location web
pnpm install --frozen-lockfile
pnpm runtime:check
pnpm dev
```

Open `http://localhost:3000`. The launcher starts FastAPI on loopback port 8000 and Nuxt on port 3000. The development frontend obtains a server-side session for the backend-configured ID. No Telegram bot object, webhook registration or admin DM runs while `WEBHOOK_URL` is empty. Sensitive operations still use action-specific development verification. This mode supports existing data/settings workflows, group enrollment and membership changes require Telegram connectivity.

`pnpm dev:web` starts Nuxt alone, `uv run chatterlore control-plane-dev` from the repository root starts the backend alone. Nuxt proxies `/api/control`, `/auth`, `/telegram/webhook` and `/health` to `CHATTERLORE_API_DEV_ORIGIN` (default `http://127.0.0.1:8000`). If you change the API port, set that variable to the matching origin in the frontend shell. Keep `CONTROL_PLANE_DEV_ORIGIN` equal to the exact browser origin, `localhost` and `127.0.0.1` are different origins.

To opt into Telegram-connected development, configure a valid bot token, complete public HTTPS `WEBHOOK_URL` ending in `/telegram/webhook` and non-placeholder `WEBHOOK_SECRET`. The backend registers a real webhook, clears the default command menu and enables owner-alert delivery. This changes the bot's update delivery, stop any competing writer first. An incomplete connected configuration fails before Telegram calls.

## Deploy the control plane

Follow [Deployment](deployment.md) for a public HTTPS origin, DNS/ports, Caddy, persistent volumes and Telegram OIDC/Mini App configuration. Production requires `CONTROL_PLANE_ENABLED=true`, a token, OIDC client credentials and a valid vault master-key ring. Development identity settings do not relax production authentication.

Sign in as a bot owner, open Chats and enroll a group before expecting its messages to enter memory. You or another effective bot owner must administer the selected Telegram group, and the bot must be a member. Then enable learning for the desired chat/topic. Adding the bot to Telegram and enabling learning alone do not enroll it when managed-chat enforcement is active.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| No `/help` reply | Token, process logs, network access, group membership and whether another poller/webhook owns updates |
| Commands work but ordinary messages are missing | BotFather privacy, bot admin membership, correct topic, `/learn_status`, user allow/deny rules and active enrollment when the control plane is enabled |
| Stored messages are skipped | `/memory_debug`, it reports policy reasons without message bodies |
| Search says the index is not ready | Provider/model identity and the explicit embedding migration workflow, do not label old vectors with a new model |
| Ask reports insufficient context | Query the same topic where the fact was learned, confirm eligible messages/notes exist and inspect search before tuning `ANSWER_MIN_SCORE` |
| Provider authorization/quota error | Correct endpoint, supported models, process-exported custom key references and provider account quota, `doctor` does not make a paid test call |
| UI has no owner pages | Development ID matches `BOT_ADMINS`, or the production Telegram identity has a current owner grant |
| UI API requests fail | Backend is running, exact browser origin and proxy port match, production webhook/OIDC configuration is complete |
| Telegram sign-in is rejected | Inspect the `control_plane_auth` reason in backend logs, reopen the Mini App for fresh launch data when it has expired. Optional profile details are not required |
| `.env` edits appear ineffective | Restart after environment edits, remove overriding process variables, inspect persisted runtime and scoped overlays through settings |
