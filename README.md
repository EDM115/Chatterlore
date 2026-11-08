<p align="center">
  <img src="docs/assets/brand/chatterlore-logo.png" alt="Chatterlore logo" width="160">
</p>

<h1 align="center">Chatterlore</h1>

<p align="center">A self-hosted Telegram bot that remembers group conversations and answers questions from them.</p>

<p align="center">
  <a href="https://github.com/EDM115/Chatterlore/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/EDM115/Chatterlore/actions/workflows/ci.yml/badge.svg"></a>
  <img alt="Python 3.12+" src="https://img.shields.io/badge/python-3.12%2B-blue.svg">
  <img alt="License MIT" src="https://img.shields.io/badge/license-MIT-green.svg">
</p>

Chatterlore collects messages where admins enable learning, summarizes them into durable memory and retrieves that memory for `/ask` and `/memory_search`. It keeps chats and forum topics separate, supports manual notes and reviewed imports, and includes a web interface for administration.

SQLite holds messages, memory and operational state. Markdown files provide readable, rebuildable copies of durable memory. You can use OpenAI-compatible APIs, an existing local model server or embedded local models, with separate providers for answers, summaries and embeddings.

## Start here

You need Git, [uv](https://docs.astral.sh/uv/getting-started/installation/) and Python 3.12 or newer. uv can install Python for you. The commands below use PowerShell 7, run them from the repository root.

```powershell
git clone https://github.com/EDM115/Chatterlore.git
Set-Location Chatterlore
uv python install 3.12
uv sync --frozen --python 3.12 --extra dev
Copy-Item .env.example .env
uv run chatterlore migrate
uv run chatterlore doctor
```

The default `AI_PROVIDER=fake` lets you check the installation without an AI account or model download. It produces deterministic test output. Follow [Setup](docs/setup.md) to configure a real AI provider, create your Telegram bot, enable learning and verify an answer from a known message.

After setting `TELEGRAM_BOT_TOKEN` and your numeric `BOT_ADMINS` in `.env`, start the bot:

```powershell
uv run chatterlore polling
```

Add the bot to a group, allow it to receive ordinary messages through BotFather privacy settings or group-admin membership, then send `/allow_chat` in that group (or `/allow_topic` inside one forum topic). Send a useful fact as an ordinary message, then try `/memory_search <question>` and `/ask <question>`.

## What to know before enabling learning

- A chat scope includes its topics, a topic query stays within that topic. Cross-chat retrieval requires a named memory profile and an explicit destination permission.
- Learning starts disabled. The bot excludes skipped messages from embeddings, summaries and retrieval, but can still store their text and skip reason in local SQLite. Read [Privacy](docs/privacy.md) before adding it to a group.
- External answer, summary and embedding providers can each receive conversation content. Redaction masks common secret and personal-data patterns before OpenAI-compatible requests, it does not anonymize the local database.
- Imports require a reviewed local Telegram Desktop JSON/HTML archive. The Telegram Bot API does not provide arbitrary group-history backfill.
- Changing an embedding provider or model on populated memory requires an explicit index migration. Changing an answer model does not rebuild embeddings.

## Web administration and deployment

The control plane provides Dashboard, Bot Settings, Providers, Chats, Query, Memory, Stats, Audit and Operations pages. It uses Telegram identity and checks bot-owner or chat-scoped authority for each operation. Bot owners can manage encrypted provider credentials and enroll, pause, resume or detach chats.

For offline UI development, set `CONTROL_PLANE_DEV_USER_ID` and `BOT_ADMINS` to the same positive test ID in `.env`, leave `WEBHOOK_URL` empty and run `pnpm install --frozen-lockfile` followed by `pnpm dev` from `web/`. Install the pnpm release declared in `web/package.json`, pnpm provisions the declared Node runtime. This mode uses a local development identity and makes no Telegram API calls.

For a public instance, follow [Deployment](docs/deployment.md). The Compose stack runs FastAPI and Nuxt behind Caddy with persistent SQLite/Markdown data, HTTPS, a Telegram webhook, browser OIDC and Mini App authentication. Complete the [release checklist](docs/release.md) for the deployment you intend to publish.

## Documentation

| Task | Guide |
| --- | --- |
| Install from scratch and verify a working bot | [Setup](docs/setup.md) |
| Understand scopes, learning, memory, roles and profiles | [Concepts](docs/concepts.md) |
| Configure environment variables, AI routes and local models | [Configuration](docs/configuration.md) |
| Use Telegram and local CLI commands | [Commands](docs/commands.md) |
| Understand stored data and outbound transfers | [Privacy](docs/privacy.md) |
| Deploy with polling, webhooks or Docker | [Deployment](docs/deployment.md) |
| Back up, restore and maintain an instance | [Operations](docs/operations.md) |
| Store and rotate credentials | [Secrets](docs/secrets.md) |
| Work on the code and run checks | [Architecture](docs/architecture.md), [Testing](docs/testing.md) |
| Verify a release | [Release checklist](docs/release.md) |
| Reuse the logo | [Branding](docs/branding.md) |

## License and attribution

Chatterlore uses the [MIT License](LICENSE). Project by [@EDM115](https://github.com/EDM115), with help from Codex. The logo assets were generated with OpenAI image generation.
