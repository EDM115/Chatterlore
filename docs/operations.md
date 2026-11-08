# Operations

Run commands from the repository root with the deployment's configuration loaded. Examples use PowerShell and `uv run`, inside the bot container use `pzp-bot` directly. CLI access grants local operator authority, restrict the host and its environment. See [commands](commands.md) for syntax and [configuration](configuration.md) for settings.

## Health and logs

```powershell
uv run pzp-bot show-config
uv run pzp-bot doctor
docker compose ps
docker compose logs --tail 100 bot
```

`show-config` masks secrets and database URL credentials. `doctor` checks bootstrap configuration, provider dependency/key presence and local paths without starting Telegram or making a paid inference call. It does not prove provider authentication, local inference, webhook reachability or browser login. Its bootstrap OpenAI-compatible key check can report a missing key for a no-auth local-server profile, use an explicit selected-profile check to establish that route's operation.  
Application JSON logs include timestamps, identifiers, update type, duration and safe error metadata. `telegram_rate_limited` and `telegram_api_error` distinguish delivery trouble from provider failure. Logs/audits still contain identifiers, restrict their access and retention. Successful AI usage does not prove Telegram received the answer.  
Webhook `GET /health` returns 200 while `pzp-idle-summarizer` and `pzp-runtime-settings-refresher` are running, and 503 if either handle stops. It checks liveness, repeated iteration failures can continue while health stays 200. Monitor `background_task_iteration_failed` (task, exception type, failure count, backoff) and `background_task_stopped_unexpectedly`. Supervised loops retry ordinary exceptions with bounded exponential backoff and reset the count after success.  
Bot runtimes also run the embedding-index worker. Control-plane runtimes deliver admin alerts, this non-critical worker appears separately in health. Polling has no HTTP server, use process supervision and structured events.

## Audit and diagnostics

```powershell
uv run pzp-bot audit-events --limit 100 --scope all --format jsonl --out data/audit.jsonl
uv run pzp-bot memory-status --scope=-1001234567890
uv run pzp-bot runtime-settings
```

Chat admins can review `/audit_events limit=100 scope=this_chat`, bot admins can request `scope=all`. Audit events contain operational metadata, use memory export for durable knowledge. Usage statistics apply the global/chat/topic hierarchy and aggregate counts, errors, latency and operations in SQL. Unrecognized legacy usage scope contributes only to global totals.  
Dashboard, Stats, Operations and bot-admin `GET /api/control/v1/operations/health` inspect bounded state without inference or downloads. Explicit CSRF-protected POST checks under `/api/control/v1/operations/checks/` perform the following work and record content-free audit events:

| Check | Work and side effects |
| --- | --- |
| `db-integrity` | SQLite `PRAGMA integrity_check` |
| `projection-status` | Bounded read-only Markdown inspection |
| `provider-profile` | Constant-input selected-profile chat/embedding call, which may consume quota |
| `local-model-readiness` | Dependency/load state, preparation only with explicit `allow_download=true` |

Audit/usage APIs use descending keyset pagination with at most 100 rows, usage responses exclude request metadata. Synthetic provider checks establish function, measure recall, quality and capacity separately.

## Backup, upgrade and restore

Back up SQLite, record the image/source and schema revision, and retain the matching environment and vault keys separately. Markdown is rebuildable, retaining a copy helps compare recovery results. A database backup without its referenced master keys cannot recover managed credentials. A restore can also restore sessions/grants, protect backups like the live store.  
`build_context` and the database maintenance commands apply schema upgrades. For a pre-upgrade recovery point, run `db-backup` from the existing release before installing the new one. If that release is unavailable, save this SQLite online-backup script outside the tracked source and run it with `uv run python path/to/backup.py` before running a new application command:

```python
import sqlite3
from pathlib import Path

source = Path("data/bot.db").resolve()
backup = Path("data/backups/before-upgrade.sqlite").resolve()
backup.parent.mkdir(parents=True, exist_ok=True)
with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as connection:
    with sqlite3.connect(backup) as destination:
        connection.backup(destination)
```

Use the deployment's actual path and a new backup filename. A direct copy of a live SQLite file can omit WAL state, use online backup or stop all writers before copying. Normal maintenance on the installed release uses:

```powershell
uv run pzp-bot db-integrity
uv run pzp-bot db-backup --out data/backups/pzp.sqlite
uv run pzp-bot db-stats
```

Integrity should report `integrity_check: ok`. Rehearse restore into a separate directory/database URL with matching keys, never use the live data mount. Run migration/integrity and [schema drift checks](testing.md#migration-drift), inspect scope counts/index coverage, verify a synthetic encrypted credential with correct keys and fail-closed behavior on a copy missing its referenced key.  
For actual recovery, stop the bot and other writers, preserve the failed store, restore the consistent backup and start the compatible image with its keys. Check health, authority/session state, projections and provider operation before returning traffic. An older image needs a verified schema compatibility or downgrade procedure.  
`uv run pzp-bot db-vacuum` reclaims space after deletion, use a quiet maintenance window and a current backup. Bind-mount permissions must let the non-root container user write the database and its parent directory for SQLite sidecar files.

## Runtime settings and managed chats

```powershell
uv run pzp-bot runtime-settings --set retrieval_top_k=8 --set answer_min_score=0.2
uv run pzp-bot runtime-settings --reload
```

Safe database-backed defaults refresh at `RUNTIME_SETTINGS_REFRESH_SECONDS`. `/runtime_settings reload=on` requests immediate refresh for bot admins. Use `/settings key=value` to preview scoped changes and `/settings confirm=<token>` to apply, `/memory_settings` applies supported edits directly for authorized admins. Secrets belong in deployment configuration or the vault.  
The control plane shows effective inherited values separately from exact overrides. **Reset to inherited** removes an override so later parent changes flow through. A stale version returns 409, reload/review before retrying. Populated embedding-route changes require an index migration.  
`BOT_ADMINS` supplies bootstrap recovery owners. Managed owner changes apply without restart, removal revokes that user's sessions and cannot remove a bootstrap owner or the final effective owner. Only bot admins manage delegated grants, the target must be a non-bot chat member. Native/delegated admins operate within their active managed chats. `ADMIN_CACHE_TTL_SECONDS` controls native administrator caching (300 by default, 0 disables caching).  
Enroll from Chats with a fresh requester-bound Mini App selection or the button in that requester's bot DM. The backend rechecks requester authority, group type and bot membership. An expired request requires a new selection.

| Action | Result |
| --- | --- |
| Pause | Stops managed-chat ingestion and idle summarization, retains memory |
| Resume | Reactivates the chat |
| Detach | Removes scoped administration, retains memory and Telegram membership |
| Leave | Calls Telegram to leave, then detaches |
| Forget | Deletes selected application messages/knowledge/vectors and reconciles Markdown after owner verification and exact confirmation |

Forget does not delete Telegram chat history, rehearse on a disposable scope. [Privacy](privacy.md) explains backups/audit retention and why learning consent differs from raw storage. Browser **Log out** revokes the current session, the Mini App has no logout control. Stable session CSRF supports multiple tabs, refresh after `csrf_rejected` before retrying.

## Summary and projection recovery

`summary_failed` leaves sources pending and creates no durable knowledge from the failed summary. Review safe scope/source/provider/model metadata and usage, correct the cause and retry:

```powershell
uv run pzp-bot summarize-now --scope=-1001234567890
```

A zero result means no pending batch completed. A claim holds at most `summary_batch_size` messages for 900 seconds. Failure releases it, a later worker reclaims abandoned stale claims. Repeated `summary_stale_claims_recovered` events warrant checking provider latency and worker termination.  
SQLite `knowledge_items` is authoritative. Startup reconciles daily Markdown without inference, fix permissions/free space/unexpected paths and restart after `markdown_projection_failed`. Do not hand-edit generated files as a source-of-truth repair. Forget and knowledge retention reconcile projections while keeping audit metadata.  
Canonical snapshots reuse matching fingerprints. Refresh failure retains the previous valid snapshot for retry, source/settings drift discards stale output. After backup and cost/privacy review, save this repair script outside the tracked source and run it with `uv run python path/to/repair.py`:

```python
import asyncio
from pzp_ai_bot.app_context import build_context
from pzp_ai_bot.config import get_settings

async def repair():
    context = await build_context(get_settings())
    try:
        print(await context.memory.compact_memory(force=True))
    finally:
        await context.memory.provider_registry.aclose()
        await context.session_factory.kw["bind"].dispose()

asyncio.run(repair())
```

A forced rebuild can call the summary provider for every non-empty scope, routine compaction reuses matching snapshots.

## Imports and exports

Review the source, target scope and embedding provider first. Telegram's Bot API cannot backfill arbitrary chat history, use reviewed Desktop archives or JSONL memory files.

A memory JSONL file is UTF-8 text with one JSON object per nonblank line. A minimal `data/import.jsonl` contains:
```jsonl
{"content":"Project Juniper planning is Tuesday at 10:30 UTC."}
```
`text` is an alias for `content`. Optional `kind` defaults to `imported_note`, optional `source_message_ids` is a list of integer Telegram message IDs:
```jsonl
{"kind":"decision","content":"Mira coordinates the release.","source_message_ids":[81,94]}
```
The command's `--chat-id` and `--thread-id` determine placement, an exported `scope` field does not override them. JSONL import reads these content/kind/source-ID fields, it does not restore raw Telegram messages, original database IDs/timestamps, note metadata, settings, permissions or credentials. Use database backup/restore for full recovery, and review source references when moving memory to another chat.  

```powershell
uv run pzp-bot import-telegram --file "C:/exports/chat" --chat-id=-1001234567890
uv run pzp-bot import-telegram-html --file "C:/exports/topic/messages.html" --chat-id=-1001234567890 --thread-id 5
uv run pzp-bot import-telegram-json --file "C:/exports/chat/result.json" --chat-id=-1001234567890
uv run pzp-bot import-jsonl --file data/import.jsonl --chat-id=-1001234567890
uv run pzp-bot memory-status --scope=-1001234567890
uv run pzp-bot export-memory --format jsonl --scope=-1001234567890 --out data/export.jsonl
```

`import-telegram` detects the format and prefers JSON in a directory. HTML follows validated local pagination, rejects remote/traversal/symlink/cyclic paths and does not execute scripts or open attachments. Limits are 512 MiB and 100,000 rows, HTML applies them to the pagination chain including service blocks and preflights before inference/writes. Keep files unchanged during import, unknown numeric HTML author IDs stay NULL.  
Whole-forum archives without reliable topic metadata stay aggregate chat memory. For an individual-topic archive provide the intended `--thread-id` (General is 1), replies/display names do not prove provenance. The same message IDs in another target scope represent a separate import and need review.  
Imports embed at most 100 texts per batch outside transactions and commit independent checkpoints. A later failure retains successful batches. Inspect `jsonl_import_batch`, `telegram_json_import_batch` or the corresponding failure event, correct the cause and retry the original command. JSONL tuples and exact-scope Telegram IDs skip committed duplicates, including JSON/HTML retries.  
CLI/HTTP exports stream bounded chunks. Telegram sends deterministic UTF-8-safe document parts below 45 MiB, retain/delete every part as one set. Browser downloads use private/no-store headers and disconnect cleanup, files remain plaintext. Item detail shows durable knowledge, exact raw sources require a separate audited action capped at 100 messages.

## Retrieval and Telegram delivery

`retrieval_candidate_limit` defaults to 5,000 per authorized source scope, durable knowledge uses the quota before recent learned messages. `retrieval_top_k` separately controls returned results. Measure recall/latency before tuning, broad memory profiles multiply work. If required recall falls outside the bounded scan or latency stays too high, evaluate a native vector index.  
Private Ask uses drafts and native Stop, groups/topics edit one progress message. Draft cadence is at least one second, group edits at least three seconds. OpenAI-compatible streams have a 120-second deadline and 32,768-character cap. A partial stream cannot restart paid generation, interrupted/stopped output receives an explicit acknowledgement. Other providers can return completed replies.  
Rich output splits at logical boundaries and uses literal fallback after definite rich-format rejection. Ambiguous network failure does not create another possibly duplicated reply. Rate limits suppress previews, finalization waits at most ten seconds for an existing cooldown. A webhook restart can interrupt an acknowledged Ask, process-owned answer tasks are not durable jobs.  
Connected startup clears the default command menu, manually typed commands and `/help` remain. Custom chat/language lists stay owner-managed and clients can cache menus, inspect the exact `getMyCommands` scope/language if an old list persists.

## Provider pool and vault recovery

Providers shows active/cooldown/disabled counts, masked counters and sanitized alerts. Explicit constant-input credential checks can incur charges and test that selected credential, production calls follow bounded pool rotation. Authentication/permission/allowlisted exhausted-quota errors disable a key, transient errors and other rate limits use retry/cooldown. Invalid request/model errors and unknown exceptions leave key health unchanged.  
Correct/replace the credential, re-enable if appropriate, run a selected-key check and confirm production uses a healthy sticky key. Expired cooldown allows selection, subsequent success records recovery. Exhaustion returns a controlled error after at most ten sources. Alert delivery retries temporary failures, caps Telegram retry-after at one hour and records unreachable recipients. After fixing DM access, a bot admin can retry undelivered alerts.  
`uv run pzp-bot secrets status` reports referenced key IDs/counts. Follow [rotation](secrets.md#rotation) to add a key, back up, rewrap and verify before retiring one. Retain old recovery material while retained backups still need it.

## Embedding index migrations

Back up before schema or route changes. Schema `0019_embedding_spaces` retains old vectors as `legacy_unverified`, names/dimensions do not establish original endpoint/revision provenance. Rebuild from authoritative text by default, old memory can remain unavailable for search until activation.  
The bot-admin Embedding indexes page and CLI use the same service. Status/preview perform metadata/SQL checks without inference/downloads. Answer/summary model changes do not rebuild embeddings, populated embedding-route edits fail with migration-required.

1. Configure a target embedding-capable profile/model and endpoint/credentials in [configuration](configuration.md), prepare embedded artifacts separately
2. Preview hierarchy, source count, target coverage and cost, a chat includes topics and `all` includes every chat
3. Start a job, the worker advances batches of at most 32 sources with persisted progress and no text copies
4. Inspect the latest version, correct failures and resume, cancellation fences in-flight work and expired leases permit recovery
5. Review coverage/settings before activation, enter exact `ACTIVATE <job_id>` and the current version, web activation requires fresh action-specific verification
6. Retain the old index, rebuild missing/stale coverage before rollback activation

```powershell
uv run pzp-bot embedding-index status --scope all
uv run pzp-bot embedding-index preview --profile target --scope all
uv run pzp-bot embedding-index start --profile target --scope all
uv run pzp-bot embedding-index run 1 --max-batches 1
uv run pzp-bot embedding-index status --job-id 1
uv run pzp-bot embedding-index resume 1 --version 3
uv run pzp-bot embedding-index cancel 1 --version 4
uv run pzp-bot embedding-index activate 1 --version 5 --confirmation "ACTIVATE 1"
```

IDs/versions illustrate separate actions, use returned values and activate a complete current job. Activation rechecks coverage/configuration and changes route/epoch atomically, descendant overrides remain. Explicit query routes can need reindex-only jobs covering every authorized source in that space. Missing coverage fails with migration information instead of omitting topics or comparing incompatible vectors. Deletes remove vectors across spaces, jobs recheck source existence/text before commit.

```powershell
uv run pzp-bot local-model prepare --profile local --mode embedding
uv run pzp-bot local-model prepare --profile local --mode chat --allow-download
```

Preparation differs from inference. Default preparation uses cached/offline artifacts, downloads require explicit authorization, `local_files_only=false`, known sizes and the deployment byte cap. Preparation pins Hub revisions and inference uses offline artifacts, local directories are fingerprinted on reload. No-auth local HTTP profiles cannot inherit a cloud key or fall back remotely.

### Evidence-bound legacy adoption

Adopt only with retained original provenance. A manifest maps 1–100 legacy embedding IDs to original source/vector-byte SHA-256 values, plus scope, target profile/model/dimension and original configuration fingerprint. Current text/names/dimensions do not prove historical provenance, retain the original evidence separately.  
Supply a JSON object with these fields, unknown fields are rejected:

| Field | Required value |
| --- | --- |
| `scope` | Authorized scope string such as `-1001234567890:5` |
| `provider_profile` | Target embedding profile name |
| `model` | Original embedding model ID |
| `dimension` | Positive integer dimension, at most 65536 |
| `original_config_hash` | Original semantic-configuration SHA-256 fingerprint (64 lowercase hex characters) |
| `entries` | List of 1–100 objects, each with a unique positive `embedding_id`, original `source_sha256` and original stored-vector-byte `vector_sha256` (64 lowercase hex characters each) |

Use evidence retained when those embeddings were created, calculating hashes from today's text and model selection cannot establish original provenance.  
`embedding-index adopt-preview --manifest PATH` validates without inference or vector mutation. Review its hash, attest provenance, then commit with `--preview-hash HASH --confirmation "ADOPT HASH" --attest-provenance`. Web commit requires bot-admin authority and fresh `embedding:adopt` verification, re-enter/re-preview the manifest and confirmation after browser navigation.  
Commit revalidates actor/scope/configuration/epoch, source eligibility/text, vector bytes/finiteness/dimensions and target collisions under the durable write fence. It adopts the whole batch or nothing, preserves vector bytes, leaves the active route unchanged and cannot revive deleted sources. Unknown provenance requires rebuilding, hashes support trusted-operator attestation rather than independent proof of authenticity.
