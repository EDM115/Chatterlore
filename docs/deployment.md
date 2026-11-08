# Deployment

Choose polling for bot-only operation or the Caddy/Nuxt/FastAPI stack for the browser and Mini App control plane. Complete [setup](setup.md), select providers in [configuration](configuration.md) and prepare [backups and secrets](operations.md#backup-upgrade-and-restore) before using persistent group data.

## Polling

```powershell
uv run pzp-bot polling
```

Polling needs a valid `TELEGRAM_BOT_TOKEN`. Startup deletes the current webhook with `drop_pending_updates=False`, clears the default command menu and starts one poller. It exposes no HTTP server or browser control plane. Supervise its process and the background-task events described in [operations](operations.md#health-and-logs).  
Run one Telegram update consumer per bot token. To switch a webhook deployment to polling, stop the webhook process first, start the poller, then stop it before restoring the webhook deployment. Verify the registered URL, allowed update types, pending deliveries and health afterward. Starting polling while the webhook service remains live can make the two runtimes replace each other's delivery mode.

## Webhook

For a bot-only webhook process, configure:

```env
TELEGRAM_BOT_TOKEN=<bot-token>
RUNTIME_MODE=webhook
WEBHOOK_URL=https://bot.example.com/telegram/webhook
WEBHOOK_SECRET=<deployment-specific-random-secret>
WEBAPP_HOST=127.0.0.1
WEBAPP_PORT=8080
```

```powershell
uv run pzp-bot webhook
```

Terminate public HTTPS at a trusted proxy and route `/telegram/webhook` to this process. Startup registers the URL and dispatcher update types without dropping pending updates. The endpoint rejects missing/wrong `X-Telegram-Bot-Api-Secret-Token`, startup rejects blank secrets and `change-me`, `changeme`, `placeholder`, `default` or `secret`. Use a random value accepted by Telegram, see [secrets](secrets.md).  
Probe `/health` on the backend, 200 means the summary/settings loops are running and 503 means one has stopped. Monitor repeated iteration failures even while liveness stays healthy. `/ask` text/caption commands receive acknowledgement before bounded process-local generation so Stop can arrive, a restart can interrupt an acknowledged answer and will not replay it into another paid generation.

## Public control-plane deployment

Production uses one external HTTPS origin and three containers. FastAPI owns identity/business endpoints, Nuxt serves presentation and Caddy routes both under the same origin. Only Caddy publishes host ports.

| Service | Internal listener | Persistent state |
| --- | --- | --- |
| `bot` | FastAPI `8080` | `./data` mounted at `/app/data` for SQLite and Markdown |
| `frontend` | Nuxt `3000` | None |
| `proxy` | Caddy `80/443` | Named `caddy_data` and `caddy_config` volumes |

Install Docker Engine with the Compose plugin (or Docker Desktop), create DNS for your public hostname and allow inbound HTTP/HTTPS to Caddy. Run Compose commands from the repository root. Keep a stable origin such as `https://bot.example.com`, then register the same bot in BotFather:

1. Open the BotFather Mini App, select your bot and open Login Widget. Add both the origin `https://bot.example.com` and the exact OIDC redirect URL `https://bot.example.com/auth/telegram/callback` to Allowed URLs, then copy its client ID and client secret into the backend configuration. PZP uses OIDC rather than an embedded login widget, see [Telegram Login setup](https://core.telegram.org/bots/telegram-login#registering-your-allowed-urls).
2. Under `/mybots` > your bot > Bot Settings > Configure Mini App, enable the Main Mini App and set its URL to `https://bot.example.com/`. This creates the bot-profile launch button, see [Main Mini App setup](https://core.telegram.org/bots/webapps#launching-the-main-mini-app).
3. To expose the same app from the bot DM menu, use `/setmenubutton` (or Bot Settings > Menu Button), choose the bot, enter a label such as Open control plane and use `https://bot.example.com/` as the URL. [Telegram's menu-button configuration](https://core.telegram.org/bots/webapps#launching-mini-apps-from-the-menu-button) is separate from the slash-command list.
4. Start the bot DM as each owner who needs browser enrollment buttons or health alerts. Put at least one real numeric owner ID in `BOT_ADMINS`. Keep Telegram group privacy/member access configured as described in [Setup](setup.md#start-polling-and-verify-a-group).

The required backend configuration includes:

```env
TELEGRAM_BOT_TOKEN=<bot-token>
BOT_ADMINS=<numeric-owner-id>
WEBHOOK_URL=https://bot.example.com/telegram/webhook
WEBHOOK_SECRET=<deployment-specific-random-secret>
CONTROL_PLANE_ENABLED=true
CONTROL_PLANE_PUBLIC_ORIGIN=https://bot.example.com
TELEGRAM_OIDC_CLIENT_ID=<botfather-client-id>
TELEGRAM_OIDC_CLIENT_SECRET=<botfather-client-secret>
CONTROL_PLANE_ACTIVE_MASTER_KEY_ID=primary
CONTROL_PLANE_MASTER_KEYS={"primary":"<base64-encoded-32-byte-key>"}
```

Generate the vault key as described in [Secrets](secrets.md#vault-keys-and-encrypted-credentials) and a webhook secret with `[Convert]::ToHexString([Security.Cryptography.RandomNumberGenerator]::GetBytes(32))`. Add the chosen AI provider configuration. Supply real generated secrets, examples above are placeholders. Keep the exact external origin consistent across browser, Mini App and OIDC settings. Compose loads `.env` only into the bot service, custom API-key environment references work there through `env_file`. The frontend receives only listener/Node settings, Caddy receives its site address.  
Compose fixes `DB_URL=sqlite+aiosqlite:///data/bot.db`, `MEMORY_DIR=data/memory`, `RUNTIME_MODE=webhook`, host `0.0.0.0` and port `8080` inside the bot. A different container database location requires a matching Compose override/mount. Ensure the non-root user can create SQLite sidecars under the mounted directory. The stock backend image omits the heavy `local` extra, embedded inference needs a deliberately built image with those dependencies and persistent model artifacts, or use an allowlisted local HTTP server profile.  
`CADDY_SITE_ADDRESS` defaults to `CONTROL_PLANE_PUBLIC_ORIGIN`. Caddy handles TLS for a normal public hostname. For an external TLS terminator such as cloudflared, set `CADDY_SITE_ADDRESS=:80` only with a trusted private/local Caddy listener, retain the exact external HTTPS origin in application configuration. `PZP_HTTP_PORT` and `PZP_HTTPS_PORT` change published port numbers, they do not restrict bind addresses or change internal ports. Use a Compose override to bind the ingress to loopback when a local tunnel owns public access. An ephemeral tunnel hostname needs matching Telegram/OIDC registration and is not a stable production address.

```powershell
docker compose config --quiet
docker compose build
docker compose run --rm bot pzp-bot doctor
docker compose run --rm bot pzp-bot migrate
docker compose up -d --wait
docker compose ps
docker compose logs --tail 100 bot
```

Inspect the resolved configuration locally if needed, plain `docker compose config` expands environment values and can disclose secrets in terminal/log output. The `.env` file is optional for offline image inspection, starting this production stack still requires complete live settings. Startup also applies migrations, take the pre-upgrade backup before any new-image application command. `doctor` checks local configuration/path/dependency state and does not verify live authentication.  
Open the public origin in a browser and sign in with the Telegram identity listed in `BOT_ADMINS`, then open the bot's Main Mini App and confirm it reaches the same owner pages. In Chats, enroll a group you administer with the requester-bound selection, the bot must already be a member. Activate the chat, enable learning for its intended scope and repeat the known-fact search/answer from [Setup](setup.md#start-polling-and-verify-a-group). Enrollment and learning are separate requirements.  
The Python image uses multi-stage `python:3.12-slim`, frozen non-editable installation and non-root `pzp`. The Nuxt image builds frozen dependencies on Node major 26, copies `.output` into a non-root Node runtime and probes `/login`. Caddy uses `caddy:2-alpine`. Standalone installers and base-image tags can change, record resolved versions and image digests for release/recovery. Application dependency locks remain frozen.  
The bot image's generic healthcheck runs `doctor`, Compose replaces it with HTTP `/health`. Caddy's check validates its config, frontend probes `/login`. Healthy container status alone does not verify external TLS, routes, identity or Telegram delivery.

## Routing and deployment verification

Caddy forwards exact `/api/control` and `/auth` plus descendants, exact `/telegram/webhook` and exact `/health` to FastAPI. Other requests reach Nuxt, including its internal icon API. Verify that reserved backend paths produce backend responses instead of frontend HTML. Before forwarding to Nuxt, Caddy removes `Cookie`, `Authorization` and `X-CSRF-Token`. Do not add backend credentials to the frontend image, environment or build arguments.  
Check public `/`, `/login`, `/health`, anonymous session denial, webhook-secret rejection, absent development-auth routes, HTTPS redirect, HSTS/CSP and private/no-store documents. Complete browser and Mini App authentication with the registered callback. Verify real message delivery, stable sessions, management permissions and independent frontend replacement using the [release checklist](release.md).  
Build and replace one application service without rebuilding the other:

```powershell
docker compose build frontend
docker compose up -d --no-deps frontend
docker compose build bot
docker compose up -d --no-deps bot
```

Run frontend build/asset/SSR gates before replacement, back up before backend migration and preserve exact rollback images. After replacement verify public routes, service health, current browser/Mini App session behavior and Telegram updates. `docker compose down` stops the stack, keep persistent volumes/data unless intentionally retiring the deployment.

## Loopback development

From `web/`, `pnpm dev` coordinates the dedicated FastAPI development command and Nuxt. Set a positive `CONTROL_PLANE_DEV_USER_ID`, exact loopback origin and disposable paths using [setup](setup.md). `pnpm dev:web` runs Nuxt alone, `pnpm build` then `pnpm start` runs only production Nuxt output.  
With an empty `WEBHOOK_URL`, combined development makes no Telegram API calls even if a token is present. Database/settings/health stay available, enrollment, membership verification and leave fail as unavailable. Connected development requires a complete public HTTPS webhook URL, safe secret and token, it changes Telegram delivery mode and enables real membership/leave/DM operations. Use a separate disposable bot when possible and coordinate its sole update consumer. Mock login exists only in the dedicated command, production webhook startup still requires its HTTPS/OIDC/master-key configuration.
