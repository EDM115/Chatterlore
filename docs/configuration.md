# Configuration

Run backend commands from the repository root. PZP AI reads environment variables and the root `.env`, process variables take precedence. Unknown keys are ignored and empty values leave defaults in effect. Restart the backend after changing deployment configuration. Safe runtime settings and scoped overlays persist in SQLite and can override environment defaults, see [Concepts](concepts.md#settings-precedence).

`.env.example` is a starting configuration. The tables below list application defaults, deployment-specific variables are separate. Keep secrets out of Git, images, Telegram commands and frontend environment variables. Use `uv run pzp-bot show-config` for non-secret diagnostics and [Secrets](secrets.md) for key storage and rotation.

## Telegram, storage and logging

| Variable | Default | Purpose |
| --- | --- | --- |
| `TELEGRAM_BOT_TOKEN` | Empty | BotFather token, required for polling, webhook and production control-plane use |
| `BOT_ADMINS` | Empty | Comma-separated numeric bootstrap owner IDs |
| `RUNTIME_MODE` | `polling` | Runtime metadata, the `polling` or `webhook` CLI command selects the running mode |
| `WEBHOOK_URL` | Empty | Complete public HTTPS delivery URL, including `/telegram/webhook` |
| `WEBHOOK_SECRET` | `change-me` | Webhook header secret, replace the placeholder before connected/webhook startup |
| `WEBAPP_HOST` | `0.0.0.0` | Webhook HTTP listener, keep it behind the ingress |
| `WEBAPP_PORT` | `8080` | Webhook HTTP port |
| `DB_URL` | `sqlite+aiosqlite:///data/bot.db` | SQLite database, relative to the backend working directory |
| `MEMORY_DIR` | `data/memory` | Generated daily Markdown and `MEMORY.md` |
| `LOG_LEVEL` | `INFO` | Structured log verbosity |
| `ADMIN_CACHE_TTL_SECONDS` | `300` | Process-local Telegram chat-admin cache lifetime, `0` disables reuse |

Use SQLite for the documented deployment and maintenance workflows. For an absolute database path, use `sqlite+aiosqlite:////srv/pzp/bot.db` on Linux or `sqlite+aiosqlite:///C:/pzp/data/bot.db` on Windows. Back up existing state before starting code that can migrate it.

## AI defaults and learning

| Variable | Default | Purpose |
| --- | --- | --- |
| `AI_PROVIDER` | `fake` | `fake`, `openai_compatible` or embedded `local` for the default profile |
| `AI_PROVIDER_PROFILE` | `default` | Fallback named provider profile |
| `AI_PROVIDER_PROFILES` | Empty | JSON object or list of named profile definitions |
| `AI_CHAT_PROVIDER_PROFILE` | Empty | Answer route, falls back to the selected general profile |
| `AI_SUMMARY_PROVIDER_PROFILE` | Empty | Summary route, falls back to the selected general profile |
| `AI_EMBEDDING_PROVIDER_PROFILE` | Empty | Embedding route, falls back to the selected general profile |
| `CHAT_MODEL` | `fake-chat` | Default chat model, also the initial summary-model default |
| `EMBEDDING_MODEL` | `fake-embedding` | Default embedding model |
| `OPENAI_BASE_URL` | `https://api.openai.com/v1` | Default OpenAI-compatible endpoint |
| `OPENAI_API_KEY` | Empty | Default remote credential, this built-in setting can come from `.env` |
| `REASONING_LEVEL` | `medium` | Requested `low`, `medium` or `high` reasoning, forwarded only when the selected adapter/model supports it |
| `ALLOWED_CHAT_MODELS` | Empty | Comma-separated permitted chat/summary model names, empty imposes no name restriction |
| `ALLOWED_EMBEDDING_MODELS` | Empty | Comma-separated permitted embedding model names, empty imposes no name restriction |
| `ALLOWED_REASONING_LEVELS` | `low,medium,high` | Allowed reasoning choices |
| `PROVIDER_RETRY_ATTEMPTS` | `3` | Bounded managed-pool attempts per operation |
| `PROVIDER_RETRY_BACKOFF_SECONDS` | `0.5` | Managed-pool retry backoff |
| `REDACT_BEFORE_AI` | `true` | Redact inputs at the OpenAI-compatible request boundary |
| `REDACTION_CUSTOM_PATTERNS` | Empty | Additional regular expressions, separated by `||` or newlines |
| `LEARNING_DEFAULT_ENABLED` | `false` | Learning fallback when no global/chat/topic policy overrides it |
| `SUMMARY_BATCH_SIZE` | `25` | Pending learned messages per automatic summary batch |
| `SUMMARY_IDLE_SECONDS` | `900` | Inactivity before the idle worker summarizes pending messages |
| `RETRIEVAL_TOP_K` | `6` | Number of results requested from eligible memory |
| `RETRIEVAL_CANDIDATE_LIMIT` | `5000` | Bound on vector candidates considered, durable knowledge is prioritized |
| `ANSWER_MIN_SCORE` | `0.05` | Minimum best retrieved score before generated answering |
| `SYSTEM_PROMPT_TEMPLATE` | `scoped` | Default answer system-prompt template |
| `RAW_MESSAGE_RETENTION_DAYS` | Unset | Raw-message retention age, unset disables this retention rule |
| `KNOWLEDGE_RETENTION_DAYS` | Unset | Durable-knowledge retention age, independent of raw-message retention |
| `REVIEW_BEFORE_COMPACT` | `false` | Require approval before proposed summaries become durable memory |
| `RUNTIME_SETTINGS_REFRESH_SECONDS` | `60` | Background reload interval for persisted safe defaults |
| `FORGET_CONFIRM_TTL_SECONDS` | `600` | Lifetime of destructive forget challenges |
| `FAKE_EMBEDDING_DIMENSIONS` | `64` | Deterministic test-vector dimensions, minimum 8 |

Review-before-compact applies to new summary proposals, it does not remove existing memory or block manual notes. Raw storage and outbound AI transfer have different boundaries, see [Privacy](privacy.md).

## Provider profiles

A provider profile defines an endpoint, model defaults, capabilities and credential source. It does not grant access to another chat's memory. You can manage profiles in the Providers page or bootstrap them with `AI_PROVIDER_PROFILES`.

For example, route chat generation to a remote provider and embeddings to an existing local server:

```dotenv
AI_PROVIDER_PROFILES='{"answers":{"provider":"openai_compatible","base_url":"https://api.openai.com/v1","api_key_env":"ANSWER_API_KEY","api_dialect":"openai","chat_model":"replace-with-your-chat-model-id","chat_capable":true,"embedding_capable":false},"vectors":{"provider":"openai_compatible","local":true,"auth_mode":"none","api_dialect":"generic","base_url":"http://127.0.0.1:11434/v1","embedding_model":"replace-with-your-installed-embedding-model-id","chat_capable":false,"embedding_capable":true}}'
AI_CHAT_PROVIDER_PROFILE=answers
AI_SUMMARY_PROVIDER_PROFILE=answers
AI_EMBEDDING_PROVIDER_PROFILE=vectors
```

Replace both model placeholders and ensure the local embedding server is running. For local CLI execution, export the referenced credential in the same terminal before starting the backend:

```powershell
$env:ANSWER_API_KEY = Read-Host 'Answer provider key' -MaskInput
uv run pzp-bot polling
```

`api_key_env` and `openai_api_key_env` read the process environment. An arbitrary key defined only in `.env` is ignored by that lookup, this also applies to an explicit `api_key_env: "OPENAI_API_KEY"` reference. Compose's backend `env_file` injects those variables into the container environment. In production, use platform secret injection or managed encrypted credentials.

| Profile field | Meaning |
| --- | --- |
| `provider` | `fake`, `openai_compatible` or embedded `local`, alias `ai_provider` |
| `chat_model`, `embedding_model` | Defaults for supported operations, disable an unused capability rather than inventing a placeholder model |
| `chat_capable`, `embedding_capable` | Boolean capability flags, both default to true |
| `reasoning_capable` | Whether reasoning options apply, defaults true for named remote OpenAI-compatible profiles and false for named local/fake profiles |
| `base_url` / `openai_base_url` | OpenAI-compatible API base URL |
| `api_key_env` / `openai_api_key_env` | Process-variable reference for a credential |
| `api_dialect` | `auto`, `openai`, `deepseek` or `generic`, choose explicitly for ambiguous endpoints |
| `local` | Explicit local-server classification, required for no-auth HTTP profiles |
| `auth_mode` | `api_key` (default) or `none`, `none` requires `local=true` and clears remote credentials |
| `redact_before_ai` | Per-profile redaction override |
| `redaction_custom_patterns` | List of regex strings or a `||`/newline-separated string, alias `redaction_patterns` |
| `local_chat_options`, `local_embedding_options` | Embedded model options below |
| `fake_embedding_dimensions` | Fake-provider vector size |

The JSON may also be a list of objects with `name`. Names are lowercase, use `a-z`, digits, `_` and `-` within 64 characters for managed profiles. Bootstrap profiles without an explicit credential can inherit the default key only at the same default endpoint, foreign endpoints require an explicit credential source. No-auth local servers never inherit the remote key.

PZP AI uses the OpenAI-compatible Chat Completions and embeddings paths. The OpenAI dialect adapts recognized reasoning models, the DeepSeek dialect maps requested medium reasoning to high, and the generic local-server dialect omits unsupported reasoning fields. Verify model capability with the Providers page's explicit test action, ordinary page loads do not run inference or paid checks.

With the control plane enabled, a remote managed profile can use an ordered pool of up to ten environment/database credential sources. Successful credentials remain sticky, classified credential or temporary failures can rotate/cool down keys within the attempt bound. Invalid requests and unknown failures do not rotate keys. This is failover within one profile, it does not redirect content to another provider. See [Operations](operations.md) and [Secrets](secrets.md).

## Local inference

Existing Ollama, vLLM or llama.cpp-compatible servers must be installed and started separately. Their HTTP adapters use the normal dependencies. A server reached from Docker needs the correct container/host address and an explicit allowed origin, `127.0.0.1` inside the bot container refers to that container.

Embedded inference runs Transformers chat models and Sentence Transformers embeddings in the backend process. Install it with `uv sync --frozen --extra dev --extra local`, then define a `provider=local` profile with only the capabilities you need. For example:

```dotenv
AI_PROVIDER_PROFILES='{"vectors":{"provider":"local","chat_capable":false,"embedding_capable":true,"embedding_model":"sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2","local_embedding_options":{"device":"cpu","local_files_only":false,"normalize_embeddings":true,"prompt_mode":"generic"}}}'
AI_EMBEDDING_PROVIDER_PROFILE=vectors
```

This example keeps the default answer/summary profile, configure those separately for useful generated text. Preparation is explicit:

```powershell
uv run pzp-bot local-model prepare --profile vectors --mode embedding --allow-download
```

Without `--allow-download`, preparation uses cached files only. Downloads also require `local_files_only=false` in that profile and must fit the deployment byte limit. Pin model/tokenizer revisions for reproducibility, use explicit model tests after preparation. A prepared artifact is not evidence of answer quality or adequate hardware capacity.

| Deployment variable | Default | Meaning |
| --- | --- | --- |
| `LOCAL_MODEL_ROOTS` | `data/models` beside the memory directory | Allowed filesystem model roots |
| `LOCAL_CACHE_ROOTS` | `data/models/cache` beside the memory directory | Allowed cache roots, first configured root is the default cache |
| `LOCAL_SERVER_ORIGINS` | `http://localhost:11434,http://127.0.0.1:11434` | Allowed exact local HTTP/HTTPS origins, no path/query/credentials |
| `LOCAL_MAX_LOADED_MODELS` | `2` | Maximum resident embedded model instances, range 1–32 |
| `LOCAL_DOWNLOAD_MAX_BYTES` | `536870912` | Maximum permitted model download size (512 MiB) |

Path/origin lists accept commas or `||`. Use absolute paths for explicit filesystem model/cache locations, every resolved path must remain inside its deployment allowlist. Unknown artifact sizes, unsafe files and oversized downloads fail closed. Remote model repository code is not enabled.

Both local option objects use the following schema. Generation and embedding adapters use the fields relevant to their operation, unsupported keys are rejected.

| Option | Default | Meaning |
| --- | --- | --- |
| `revision`, `tokenizer_revision` | Unset | Model/tokenizer revision, immutable preparation records the resolved artifact identity |
| `device` | `auto` | `auto`, `cpu`, `cuda` or `cuda:N` |
| `dtype` | `auto` | `auto`, `float32`, `float16` or `bfloat16` |
| `cache_dir` | Deployment cache root | Must remain inside `LOCAL_CACHE_ROOTS` |
| `local_files_only` | `true` | Offline loading unless explicitly disabled and download authorized |
| `batch_size` | `32` | Embedding batch size, range 1–1024 |
| `normalize_embeddings` | `true` | Normalize embedding output |
| `truncate_dim` | Unset | Optional embedding dimension limit, range 1–65536 |
| `prompt_mode` | `generic` | `generic` or model-supported `query_document` embedding prompts |
| `max_input_tokens` | `4096` | Chat input token bound, range 1–131072 |
| `max_new_tokens` | `1024` | Chat output token bound, range 1–32768, individual operations may request fewer |
| `top_p` | `1.0` | Generation sampling parameter, greater than 0 and at most 1 |
| `queue_timeout_seconds` | `30` | Positive queue wait limit, at most 3600 seconds |
| `inference_timeout_seconds` | `120` | Positive caller inference wait limit, at most 3600 seconds |
| `max_queue_size` | `8` | Admission queue size, range 0–1024 |
| `max_batch_inputs` | `1024` | Embedding input-count admission bound, range 1–65536 |
| `max_input_characters` | `262144` | Input character admission bound, range 1–16777216 |

Changing embedding semantics on existing data requires a side-by-side [embedding index migration](operations.md#embedding-index-migrations), matching dimensions alone are insufficient.

## Control-plane authentication and limits

Enable production administration only after following [Deployment](deployment.md). An enabled control plane requires an HTTPS origin, Telegram bot token, OIDC client ID/secret, a nonempty master-key map and an active key ID present in that map. Each key must be base64 encoding of exactly 32 random bytes.

| Variable | Default | Meaning |
| --- | --- | --- |
| `CONTROL_PLANE_ENABLED` | `false` | Install production authenticated administration routes |
| `CONTROL_PLANE_PUBLIC_ORIGIN` | Empty | Exact public HTTPS origin with no path, query or fragment |
| `TELEGRAM_OIDC_CLIENT_ID` | Empty | BotFather Telegram Login client ID |
| `TELEGRAM_OIDC_CLIENT_SECRET` | Empty | OIDC client secret |
| `CONTROL_PLANE_MASTER_KEYS` | Empty | JSON map of key IDs to base64 master-key values |
| `CONTROL_PLANE_ACTIVE_MASTER_KEY_ID` | Empty | Key ID used for new credential envelopes |
| `CONTROL_PLANE_AUTH_MAX_AGE_SECONDS` | `300` | Telegram authentication freshness |
| `CONTROL_PLANE_STEP_UP_MAX_AGE_SECONDS` | `300` | Fresh verification lifetime for sensitive actions |
| `CONTROL_PLANE_OIDC_TRANSACTION_TTL_SECONDS` | `600` | One-time login transaction lifetime |
| `CONTROL_PLANE_SESSION_IDLE_SECONDS` | `43200` | Session idle expiry (12 hours), minimum 60 seconds |
| `CONTROL_PLANE_SESSION_ABSOLUTE_SECONDS` | `604800` | Absolute expiry (7 days), at least the idle TTL |
| `CONTROL_PLANE_RATE_LIMIT_WINDOW_SECONDS` | `60` | Request-rate window |
| `CONTROL_PLANE_AUTH_START_RATE_LIMIT` | `10` | Login-start requests per window |
| `CONTROL_PLANE_AUTH_CALLBACK_RATE_LIMIT` | `20` | Login-callback requests per window |
| `CONTROL_PLANE_MINI_APP_RATE_LIMIT` | `20` | Mini App exchanges per window |
| `CONTROL_PLANE_STEP_UP_RATE_LIMIT` | `10` | Step-up requests per window |
| `CONTROL_PLANE_PROVIDER_TEST_RATE_LIMIT` | `5` | Explicit provider-test requests per window |
| `CONTROL_PLANE_MAX_MANAGED_PROFILES` | `32` | Managed profile count limit, at most 256 |
| `CONTROL_PLANE_MAX_CREDENTIALS_PER_PROFILE` | `10` | Total environment/database pool sources per profile, at most 10 |
| `CONTROL_PLANE_MAX_SECRET_LENGTH` | `16384` | Credential input length bound, at most 131072 |
| `CONTROL_PLANE_ALERT_DELIVERY_INTERVAL_SECONDS` | `5` | Best-effort bot-owner alert delivery interval, at most 3600 seconds |

The callback is always `<CONTROL_PLANE_PUBLIC_ORIGIN>/auth/telegram/callback`. Browser sessions use server-side revocation and CSRF checks. Sensitive mutations require exact-action fresh Telegram verification in addition to an authorized session.

## Development and frontend deployment variables

| Variable | Default | Meaning |
| --- | --- | --- |
| `CONTROL_PLANE_DEV_USER_ID` | Empty | Positive backend-configured test identity, required by `control-plane-dev` |
| `CONTROL_PLANE_DEV_ORIGIN` | `http://localhost:3000` | Exact loopback browser origin |
| `CONTROL_PLANE_DEV_API_PORT` | `8000` | Loopback FastAPI development listener |
| `PZP_API_DEV_ORIGIN` | `http://127.0.0.1:8000` | Nuxt development proxy target, set in the frontend environment |
| `CADDY_SITE_ADDRESS` | `CONTROL_PLANE_PUBLIC_ORIGIN` or `https://localhost` | Compose ingress listener/site address, can differ behind an external TLS terminator |
| `PZP_HTTP_PORT` | `80` | Compose host HTTP port |
| `PZP_HTTPS_PORT` | `443` | Compose host HTTPS TCP/UDP port |
| `NITRO_HOST`, `NITRO_PORT` | Compose uses `0.0.0.0`, `3000` | Production Nuxt listener |

Development auth exists only in the dedicated loopback runtime/build. An empty `WEBHOOK_URL` keeps combined development disconnected from Telegram. The development vault reuses a supplied key ring or stores an ignored stable key in `data/control-plane-dev-master-key`, keep that key with local development data. Production needs externally managed master keys.

## Runtime and scoped settings

Bot owners can edit safe defaults through Bot Settings, `/runtime_settings` or `pzp-bot runtime-settings --set key=value`. Editable keys are `learning_default_enabled`, `retrieval_candidate_limit`, `retrieval_top_k`, `summary_batch_size`, `summary_idle_seconds`, `summary_model`, `system_prompt_template`, `provider_profile`, `chat_provider_profile`, `summary_provider_profile`, `embedding_provider_profile`, `chat_model`, `embedding_model`, `reasoning_level`, `answer_min_score`, `forget_confirm_ttl_seconds`, `raw_message_retention_days`, `knowledge_retention_days` and `review_before_compact`. Tokens, endpoints, vault keys, authentication configuration and model path/origin allowlists are deployment settings rather than runtime settings.

Chats and Telegram `/memory_settings` or confirmed `/settings` expose the supported scoped subset. Short Telegram aliases include `top_k`, `provider`, `chat_provider`, `summary_provider`, `embedding_provider`, `reasoning`, `system_prompt`, `raw_retention_days` and `knowledge_retention_days`. `inherit` clears supported overrides, retention `off` clears the local retention value so inheritance can still apply. See [Commands](commands.md#settings-and-admin-review) for syntax and [Concepts](concepts.md#settings-precedence) for resolution.
