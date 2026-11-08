<p align="center">
  <img src="docs/assets/brand/pzp-ai-logo.png" alt="PZP AI logo" width="160">
</p>

<h1 align="center">PZP AI</h1>

<p align="center"><strong>Scoped memory for Telegram groups, built for teams that need their conversations to stay useful after the chat scrolls away.</strong></p>

<p align="center">
  <a href="https://github.com/SpamVerse/PZP-AI/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/SpamVerse/PZP-AI/actions/workflows/ci.yml/badge.svg"></a>
  <img alt="Python 3.12" src="https://img.shields.io/badge/python-3.12-blue.svg">
  <img alt="Telegram bot" src="https://img.shields.io/badge/Telegram-bot-26A5E4?logo=telegram&logoColor=white">
  <img alt="Docker ready" src="https://img.shields.io/badge/Docker-ready-2496ED?logo=docker&logoColor=white">
  <img alt="License MIT" src="https://img.shields.io/badge/license-MIT-green.svg">
</p>

PZP AI is a Telegram memory assistant that learns from live group conversations only where admins allow it, turns useful context into durable scoped memory, and answers questions with retrieval-augmented context and compact source references. It is designed for real group workflows: topic-aware memory, explicit learning controls, privacy-first defaults, provider flexibility, audit trails, Docker deployment and offline-friendly testing.

## Why Teams Use It

- Scoped by default: memory stays tied to the current chat or forum topic unless an admin explicitly enables a cross-chat profile.
- Historical imports: reviewed Telegram Desktop JSON and HTML archives share one bounded, retry-safe pipeline, Both exports prefer JSON and individual-topic imports use explicit topic targets (General is 1)
- Admin-controlled learning: enable learning per chat/topic, allow or deny users, review proposed summaries and confirm destructive actions.
- Useful answers: `/ask` retrieves durable memory, manual notes and recent learned context, then answers with source references when needed.
- Portable storage: SQLite keeps operational state, Markdown keeps durable memory readable, and exports are available as Markdown or JSONL.
- Independent AI routes: choose answer, summary and embedding profiles separately, using remote OpenAI-compatible APIs, existing local model servers or embedded Hugging Face inference with explicit capability, device, revision and offline controls. Remote profiles retain encrypted multi-key sticky failover and a bot-admin health console.
- Safe embedding changes: build a side-by-side index in resumable bounded jobs, validate current source coverage and explicitly activate it without mixing incompatible vector spaces. Legacy vectors are preserved but need an explicit rebuild before verified retrieval.
- Graphical administration: the Telegram-authenticated SSR control plane provides a live Dashboard plus Bot Settings, Providers, Chats, Query, Memory, Stats, Audit and Operations workspaces for reloadless safe defaults, encrypted multi-key credentials, scoped memory and source-backed inspection on desktop, mobile browsers and Telegram Mini Apps.
- Bounded control APIs: the authenticated backend exposes capability-aware Search/Ask, memory/review maintenance, cancellable Markdown/JSONL downloads, SQL-backed statistics, sanitized audit/usage pages and explicit diagnostics without triggering paid or local-model work during ordinary status loads; paid provider and optional local-model download checks remain separately labeled manual actions.
- Operationally ready: structured logs, audit events, runtime settings, database maintenance commands, Docker support and GitHub Actions CI are included.
- Telegram presentation: rich headings/lists/tables/code for AI answers, literal structured status replies, real streamed private drafts or throttled group/topic edits, and a quiet slash-command menu with manual command handlers retained. Providers without a streaming implementation send one completed reply

## Quick Start

Prerequisites: Python 3.12+, [uv](https://docs.astral.sh/uv/), pnpm 11+ for the Nuxt control plane, and Docker if you want container smoke tests. The frontend declares Node.js major 26+ and lets pnpm provision the runtime resolved in `web/pnpm-lock.yaml`. CI and the Python image intentionally install the latest uv rather than pinning its version.

```powershell
uv sync --extra dev --extra local
Copy-Item .env.example .env
uv run pzp-bot migrate
uv run pzp-bot doctor
uv run pytest
```

The default fake provider is deterministic and does not call external AI services, so the project is safe to explore locally before connecting Telegram or provider credentials.  
Start polling locally after you configure `.env`:

```powershell
$env:AI_PROVIDER = "fake"
uv run pzp-bot polling
```

To run the FastAPI control plane and Nuxt development server together without Telegram OIDC, set your numeric Telegram identity as `CONTROL_PLANE_DEV_USER_ID`, leave `WEBHOOK_URL` empty, then run the combined command from `web/`:

```powershell
Set-Location web
pnpm install --frozen-lockfile
pnpm dev
```

The combined command is loopback-only, creates a real server-side development session for the backend-configured identity and makes no Telegram API calls while `WEBHOOK_URL` is empty. Bot Settings, existing managed-chat controls and scoped policy can be exercised offline; group enrollment, target membership validation and leave need the explicitly connected Telegram mode. `pnpm dev:web` remains available when only the Nuxt server is wanted. See [docs/setup.md](docs/setup.md) for ports, proxy settings, optional connected Telegram mode and the production boundary.

For a real Telegram group, create a bot with BotFather, set `TELEGRAM_BOT_TOKEN`, add the bot to a group, disable BotFather privacy mode or make the bot an admin, then run `/allow_chat` or `/allow_topic` where learning should be enabled. The complete walkthrough lives in [docs/setup.md](docs/setup.md).

## Docker Smoke

```powershell
docker compose config
docker build --tag pzp-ai:local .
docker build --tag pzp-ai-web:local ./web
docker compose run --rm bot pzp-bot doctor
```

The production Compose topology runs the bot in webhook mode behind a dedicated Caddy HTTPS ingress and a separate Nuxt SSR image, only Caddy publishes host ports and the frontend/backend images can be replaced independently. For required webhook/OIDC secrets, persistent data, health checks and exclusive local polling, see [docs/deployment.md](docs/deployment.md). Snapshot-specific deployment evidence and remaining release holds are recorded in [the acceptance report](docs/release-closure-acceptance-2026-11-08.md).

## Documentation

| Need                                    | Start here                                   |
| --------------------------------------- | -------------------------------------------- |
| Install and connect a bot               | [Setup](docs/setup.md)                       |
| Learn the Telegram and CLI surface      | [Commands](docs/commands.md)                 |
| Understand the system design            | [Architecture](docs/architecture.md)         |
| Review privacy and consent boundaries   | [Privacy and data handling](docs/privacy.md) |
| Run and maintain an instance            | [Operations](docs/operations.md)             |
| Deploy with polling, webhooks or Docker | [Deployment](docs/deployment.md)             |
| Handle tokens and provider credentials  | [Secrets handling](docs/secrets.md)          |
| Run tests and CI checks                 | [Testing](docs/testing.md)                   |
| Prepare a release                       | [Release checklist](docs/release.md)         |
| Use project logo assets                 | [Branding](docs/branding.md)                 |

The roadmap and implementation history are tracked in [PLAN.md](PLAN.md). For the November resumption, start with the [full project review](docs/project-review-2026-11-07.md), [Python dependency audit](docs/dependency-review-python-2026-11-07.md) and [frontend dependency audit](docs/dependency-review-web-2026-11-07.md). Most implementation is complete, but Plan 021 remains partial until deployment, real Telegram/provider, authorization-policy and elapsed-retention acceptance are closed. The README intentionally stays focused on what the project is, how to try it, and where to go next.

## License And Attribution

PZP AI is released under the [MIT License](LICENSE). The logo concepts in `docs/assets/brand/` were generated for this project with OpenAI image generation. The codebase builds on excellent open-source tools including aiogram, SQLAlchemy, Alembic, FastAPI, Typer, Ruff, ty, pytest and uv.  
Project by [@EDM115](https://github.com/EDM115) on behalf of PZP, with help from Codex.
