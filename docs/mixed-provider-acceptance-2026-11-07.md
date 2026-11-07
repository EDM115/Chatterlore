# Mixed-provider implementation and acceptance (2026-11-07)

Implemented against clean baseline `b19244d`, verification applies to the uncommitted working tree produced by this pass. No staging, commits, branches, pushes, worktrees, deployment replacement or live-database migration occurred. The owner's CI lock-check removal and unpinned standalone Docker pnpm installer were preserved, their explicit artifact tests now describe the retained policy rather than requiring removed settings.  
## Delivered behavior

- Independent answer, summary and embedding profile routes across environment/runtime/global/chat/topic/one-shot settings, with profile-aware model defaults and nullable inherited resets. Summary one-shot text overrides cannot change the configured route used for persisted vectors
- OpenAI/DeepSeek/generic request dialects, requested/effective reasoning telemetry, explicit per-operation capabilities, fail-closed endpoint-specific credentials and no-auth local servers restricted to deployment-owned origins without remote fallback
- Optional direct Hugging Face causal generation and Sentence Transformer embeddings, typed revision/device/dtype/cache/offline/prompt/normalization controls, model-provided chat templates, safe weights and no remote repository code execution. Blocking loads/inference remain off-loop, duplicate loads, concurrency, queue admission, cancellation, busy residency, retirement and shutdown are bounded
- Explicit cached/offline preparation, immutable Hub revision resolution or filesystem artifact fingerprinting, opt-in size-capped downloads, owner-only preparation/test controls and safe metadata readiness that does not itself infer or download
- Revisions `0018_provider_operation_profiles` and `0019_embedding_spaces`, exact semantic embedding manifests, source freshness hashes, legacy preservation, SQL-only coverage, bounded resumable leased jobs, cancellation fencing and atomic versioned activation coordinated with in-flight content writers through a durable routing epoch
- Owner-only Embedding indexes UI/API/CLI/Telegram workflows, generated OpenAPI/types, command inventories and synchronized setup/architecture/operations/privacy/release documentation
## Deterministic verification

| Gate | Observed result |
| --- | --- |
| Complete offline Python suite | 571 passed, 1 credential-gated Telegram skip, 3 warnings in 221.45 seconds (Python 3.14.7) |
| Real optional local-inference slice | 25 passed, zero skips, 1 upstream warning in 5.53 seconds |
| Ruff format/lint and `ty` | Passed |
| Optional dependency lock metadata | `uv lock --check --offline` passed, no locked package versions changed |
| Disposable empty-schema migration | Upgraded through `0019_embedding_spaces`, Alembic reported no new upgrade operations, SQLite integrity `ok` |
| OpenAPI export and frontend types | Deterministic export check and `pnpm openapi:check` passed |
| Nuxt formatting/type-aware lint/types | Passed |
| Complete frontend unit/component suite | 87 tests across 17 files passed |
| Production SSR build | Passed (Nuxt 4.6.4, Nitro 2.13.6) |
| Desktop/Pixel 7 browser suite | 50 passed, no retry in the local run, 7 workers, 15.4 seconds |
| Generated asset scan | 281 text assets passed |
| Production SSR smoke | Public/protected overview/provider direct routes and privacy/CSP checks passed |

The final Python command was `uv run --no-sync pytest -q --show-capture=no --tb=short --basetemp C:/Users/lu.lederrey/AppData/Local/Temp/pzp-mixed-final-20261107-3`. An earlier integration run reported seven stale facade/router/migration-head/CLI-doc inventory failures with 564 passing tests, the explicit inventories were updated and their focused slice passed 35 tests before the final complete run above. The final rerun also includes the independent-route Telegram help regression, no behavioral assertion was removed to obtain the final pass.  
The local suite constructs real tiny tokenizer/causal-model and Sentence Transformer fixtures on disk, performs actual CPU inference and forbids external network connections. It proves adapter/load/generation/embedding behavior, not pretrained model quality, production sizing or GPU support on this host. No public checkpoint was downloaded. Local HTTP server request/auth behavior is exercised through offline contract tests, not a newly installed Ollama/vLLM/llama.cpp service.  
The new index browser regression hydrates the real production page, proves metadata/preview make no mutation, explicitly starts/runs one bounded batch, checks the exact activation confirmation and verifies navigation to fresh Telegram verification before any activation POST. A separate case proves scoped admins cannot fetch owner-only index metadata. Both run at desktop and mobile sizes. API authorization and real step-up enforcement are separately exercised by backend tests, browser requests use controlled fixtures.  
Known warnings were retained: AnyIO imports Pytest's deprecated `CallSpec2`, and Alembic/SQLAlchemy cannot reflect SQLite's existing expression-based message uniqueness index for metadata comparison. Browser tooling emitted `NO_COLOR`/`FORCE_COLOR` environment warnings. The final browser run did not reproduce the earlier intermittent icon/CSP or query-catalog/nonce errors, no CSP or error assertion was weakened. No new Python 3.12 or hosted-CI result is claimed for this snapshot.  
## Bounded live OpenAI acceptance

The replacement host `.env` key succeeded through the application provider/service boundaries against `https://api.openai.com/v1`. The key was only read for use, never printed, changed or copied into a report. All source text was synthetic, the database and memory directory were disposable and removed after cleanup, no Telegram call or production/group-data change occurred.  
The probe configured separate `answers`, `summaries` and `remote-vectors` profiles, created one synthetic note using the fake index, previewed a remote rebuild with zero provider calls, built/activated the new index, forced canonical compaction through the summary route and queried/answered through the selected embedding/answer routes. The resulting store retained two semantic spaces and returned the expected synthetic fact.  
| Call | Model | Bound/result |
| --- | --- | --- |
| Reindex one authoritative note | `text-embedding-3-small` | One short synthetic input, activated target job |
| Canonical summary | `gpt-6.1-terra` | 512 output-token cap, 102 prompt and 78 completion tokens observed |
| Query embedding | `text-embedding-3-small` | One short synthetic input |
| Ask answer | `gpt-6.1-terra` | 512 output-token cap, 103 prompt and 19 completion tokens observed, expected fact present |

Exactly four provider method calls occurred (two text, two embedding), retries were configured to one attempt, the hard probe limit was six calls and 180 seconds. Embedding token counts are unavailable in the current persisted telemetry and remain unknown, no cost estimate is inferred. This proves real OpenAI route/migration/inference integration, not live multi-vendor failover or use of different text models.  
Local evidence is retained under ignored `data/mixed-provider-acceptance-20261107/` as `probe_openai.py` and the secret-free `openai-results.json`. The canonical tracked record is this document.  
## Upgrade and remaining release boundary

Legacy vector rows lack reliable original endpoint/revision provenance and are preserved as `legacy_unverified`, no adoption is guessed from matching dimensions/model names. Explicit rebuilding from retained authoritative text is implemented, optional legacy-provenance adoption is not. Back up before upgrading and plan a first rebuild before expecting old memory to be searchable, see [the migration runbook](operations.md#embedding-index-migrations). Retained old indices are not guaranteed current after new writes, rollback requires fresh coverage validation too.  
This implementation is not deployed to the earlier live stack. No current-container reload, production image build/start, new-snapshot live Telegram group command/ingestion check, installed local-server acceptance, pretrained-model quality/performance/GPU acceptance, DeepSeek live call, real two-key failover/admin-DM test or owner-dispatched hosted CI pass is claimed. Those are explicit acceptance limits, not missing independent-routing/index code. The existing [live ledger](live-acceptance-2026-11-07.md) remains evidence for its older images only.  
