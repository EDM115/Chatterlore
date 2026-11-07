# Setup

This guide gets a local PZP AI instance ready for development, offline verification and first Telegram testing.

## Requirements

- Python 3.12 or newer (CI and the bot image use 3.12, the current offline suite is also verified on the existing 3.14 development environment)
- [uv](https://docs.astral.sh/uv/) for dependency management and command execution
- pnpm 11.28.11 for control-plane frontend development, `web/package.json` declares Node.js major 26 and pnpm provisions the lockfile-resolved runtime (currently 26.13.0)
- A Telegram bot token from BotFather for live Telegram use
- Docker Desktop if you want to run the container smoke checks

## Local Development

```powershell
uv sync --extra dev
Copy-Item .env.example .env
uv run pzp-bot migrate
uv run pzp-bot doctor
uv run pytest
```

The default `AI_PROVIDER=fake` is deterministic and does not call external AI services. Use it for local development, CI and documentation examples.
Embedded inference is optional, install `uv sync --extra dev --extra local` only on hosts that need local Transformers/PyTorch/Sentence Transformers. Existing local HTTP model servers use the ordinary remote-compatible adapter and do not require those heavyweight packages.  

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

The protected SSR shell includes browser OIDC, signed Mini App exchange, server-side sessions, capability-driven navigation, bot-admin provider/credential APIs, controlled reveal, classified sticky key-pool failover, reloadless safe runtime settings, managed bot owners and managed-chat/delegated-admin/scoped-policy workflows. Dashboard, Providers, Bot Settings, Chats, Query, Memory, Stats, Audit and Operations are implemented, with bot-wide actions restricted to bot admins and scoped actions checked against current chat/topic authority. Real-client, deployed-runtime and two-key provider acceptance remain release gates, not missing pages.

`BOT_ADMINS` remains the immutable recovery-owner set. A bootstrap owner can add reloadable database-managed bot owners in Bot Settings; removal requires exact-action Telegram step-up, revokes that user's live control-plane sessions and cannot remove a bootstrap owner or the last effective owner. The runtime form accepts only the existing safe setting allowlist and never accepts tokens, vault keys, provider secrets or OIDC configuration.

Bot owners enroll a group from Chats. Current Telegram Mini Apps use a short-lived prepared `requestChat` selection; a normal browser or older client receives the same requester-bound selection button in the bot DM and the page polls for a bounded time. Telegram verifies that the requester administers the selected group and that the bot is still a member before the chat becomes active. This flow requires the production webhook runtime or explicitly connected development mode because offline `pnpm dev` intentionally creates no Telegram bot object and makes no Bot API call.

An active managed chat accepts ingestion and idle summarization and is visible to its Telegram-native or delegated chat admins. Only bootstrap and managed bot admins can list, grant or revoke delegated chat-admin access, scoped admins retain chat/topic settings and content access but cannot inspect grants, delegate onward or revoke themselves. Paused and detached chats retain their SQLite/Markdown memory but stop new ingestion, idle summarization and scoped-admin visibility; a bot owner can resume them. Detach only stops management, leave also asks Telegram to remove the bot, and forget is a separate step-up plus exact-text destructive action that deletes the chat scope's messages, knowledge and embeddings and reconciles Markdown.

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

Telegram does not expose arbitrary historical group backfill through the Bot API. PZP AI learns live updates after consent is enabled and can import reviewed Telegram Desktop JSON/HTML exports with `pzp-bot import-telegram`, or Bot API update dumps through the retained `import-telegram-json` command. A Both-format directory prefers JSON, HTML-only archives are parsed offline without executing scripts or fetching attachments. Supply `--thread-id` for an individual-topic archive (General is topic 1), omit it only for intentional aggregate chat memory, topic membership is never guessed from replies. See [the import command contract](commands.md) and [the privacy/cost runbook](operations.md#database-maintenance).

## Independent AI Routes And Local Inference

Configure named profiles through `AI_PROVIDER_PROFILES` or the bot-owner Providers page. `AI_CHAT_PROVIDER_PROFILE`, `AI_SUMMARY_PROVIDER_PROFILE` and `AI_EMBEDDING_PROVIDER_PROFILE` set independent environment defaults, runtime/scoped settings use the same names without `AI_`. Keep `provider_profile` as the fallback if no per-operation route is selected. Profile selection supplies its own model default, a lower-precedence model from another provider is not carried across that boundary.  
Example profile JSON (put the object on one line when using a dotenv variable, never paste actual keys into the JSON):  
```json
{
  "answers": {"provider": "openai_compatible", "openai_base_url": "https://api.openai.com/v1", "api_key_env": "OPENAI_API_KEY", "api_dialect": "openai", "chat_model": "gpt-6.1-terra", "chat_capable": true, "embedding_capable": false},
  "summaries": {"provider": "openai_compatible", "openai_base_url": "https://api.deepseek.com", "api_key_env": "DEEPSEEK_API_KEY", "api_dialect": "deepseek", "chat_model": "deepseek-chat", "chat_capable": true, "embedding_capable": false},
  "vectors": {"provider": "local", "chat_capable": false, "embedding_capable": true, "embedding_model": "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2", "local_embedding_options": {"device": "cpu", "local_files_only": true, "normalize_embeddings": true, "prompt_mode": "generic"}},
  "local-server": {"provider": "openai_compatible", "local": true, "auth_mode": "none", "api_dialect": "generic", "openai_base_url": "http://127.0.0.1:11434/v1", "chat_model": "your-installed-model", "chat_capable": true, "embedding_capable": false}
}
```
Disabled capabilities need no placeholder model. Local server software and models must already exist, PZP does not install/start Ollama, vLLM or llama.cpp. A Docker host/container origin must be explicitly added to deployment-owned `LOCAL_SERVER_ORIGINS`, `local=true` alone does not authorize arbitrary HTTP access. Use `auth_mode=api_key` plus an explicit secret reference for authenticated servers, no-auth profiles never inherit `OPENAI_API_KEY`.  
Embedded chat uses `provider=local`, `chat_capable=true`, a causal `chat_model` and `local_chat_options`. Both local option objects accept revision, device (`auto`, `cpu`, `cuda`, `cuda:N`), dtype, cache directory and `local_files_only` (default true). Chat additionally uses tokenizer revision, input/output token limits, `top_p`, queue/time limits and bounded admission, embedding options include batch size, normalization, truncation dimension and generic versus query/document prompt mode. Model-provided chat templates and safe weights are required, remote repository code execution is never enabled.  
`LOCAL_MODEL_ROOTS` and `LOCAL_CACHE_ROOTS` are deployment path allowlists, use explicit absolute paths for filesystem models/caches. Defaults stay under the data/model-cache area beside the memory store. `LOCAL_MAX_LOADED_MODELS` defaults to 2 and `LOCAL_DOWNLOAD_MAX_BYTES` to 536870912 (512 MiB). Models load lazily, viewing status/catalog pages neither downloads nor runs them. Explicit preparation resolves immutable Hub revisions or fingerprints a local model directory, reload verifies those artifact bytes.  
```powershell
uv run pzp-bot local-model prepare --profile vectors --mode embedding
```
The default only prepares cached artifacts offline. To deliberately fetch a model, set that profile's `local_files_only=false` and add `--allow-download`, the provider UI requires fresh owner verification for the equivalent action. Unknown artifact sizes, unsafe files and downloads over the deployment cap fail closed. After preparation, use the explicit provider test action and the embedding migration workflow in [operations.md](operations.md#embedding-index-migrations), readiness alone is not proof of inference.  
Changing answer/summary routes is independent of stored embeddings. Changing a populated scope's embedding route/model requires an explicit migration, upgraded legacy vectors also require rebuilding from their retained authoritative source text before verified search.  

## First Verification

Run these after configuration changes:

```powershell
uv run pzp-bot doctor
uv run pzp-bot show-config
uv run pzp-bot memory-status --scope all
```

Then run `/learn_status` and `/ask <question>` in the target Telegram scope to confirm the bot sees the expected chat or topic.
