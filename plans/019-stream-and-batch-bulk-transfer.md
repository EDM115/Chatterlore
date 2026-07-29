# Plan 019: Stream and batch memory imports and exports
> **Executor instructions**: Execute after transaction boundaries and frozen dependency consumption are established. Follow every gate, use only offline fixtures, and update plans/README.md unless a reviewer owns it.
> **Drift check (run first)**: <code>git diff --stat beda837..HEAD -- pyproject.toml uv.lock src/pzp_ai_bot/memory/service.py src/pzp_ai_bot/db/repositories.py src/pzp_ai_bot/telegram/router.py src/pzp_ai_bot/main.py tests/test_memory_service.py tests/test_product_controls.py tests/test_cli.py tests/test_telegram_integration.py docs/commands.md docs/privacy.md docs/operations.md PLAN.md</code>.
## Status
- **Status**: DONE
- **Verified**: 2026-07-29 with frozen `ijson==3.5.1`, 125 focused transfer tests, repository-wide Ruff/ty gates and 218 passed, 1 skipped at 81.38% branch coverage
- **Audit finding**: lower-priority bulk transfer opportunity
- **Priority**: P3
- **Effort**: L
- **Risk**: MED
- **Depends on**: plans/008-bound-and-claim-ai-work.md, plans/013-make-lockfile-consumption-reproducible.md
- **Category**: perf
- **Planned at**: commit beda837, 2026-07-13
## Why this matters
JSONL and Telegram imports materialize complete files, embed rows serially, and keep long transactions open. JSONL export performs metadata/source queries per item and constructs one complete string before Telegram splits it. Large transfers therefore consume memory proportional to file size, issue excessive provider/database calls, and risk lock contention. Introduce streaming parsers, bounded batches, keyset export, batched embeddings, and incremental Telegram/CLI output.
## Current state
- service.py:2201-2236 accumulates all JSONL rows.
- service.py:2239-2249 reads the entire Telegram JSON file.
- service.py:1136-1217 imports complete row lists and embeds Telegram messages one at a time.
- service.py:1091-1121 loads all export items and does metadata/source lookups inside the item loop.
- router.py:995-1012 encodes and chunks only after the full export string exists.
- The official PyPI project describes ijson as a stable iterative JSON parser with Python 3.12 support; the owner-selected reviewed pin is ijson==3.5.1 and <https://pypi.org/project/ijson/> is the dependency source.
## Commands you will need
| Purpose | Command | Expected on success |
|---|---|---|
| Frozen dependency sync | <code>uv sync --frozen --extra dev</code> | exit 0 after reviewed lock update |
| Transfer tests | <code>.\.venv\Scripts\python.exe -m pytest -q tests/test_memory_service.py tests/test_product_controls.py tests/test_cli.py tests/test_telegram_integration.py</code> | all pass |
| Format/lint/type | <code>.\.venv\Scripts\ruff.exe format --check .; .\.venv\Scripts\ruff.exe check .; .\.venv\Scripts\ty.exe check</code> | each exits 0 |
| Full tests | <code>.\.venv\Scripts\python.exe -m pytest -q</code> | all offline tests pass |
## Scope
**In scope**:
- pyproject.toml
- uv.lock
- src/pzp_ai_bot/memory/service.py
- src/pzp_ai_bot/db/repositories.py
- src/pzp_ai_bot/telegram/router.py
- src/pzp_ai_bot/main.py
- tests/test_memory_service.py
- tests/test_product_controls.py
- tests/test_cli.py
- tests/test_telegram_integration.py
- docs/commands.md
- docs/privacy.md
- docs/operations.md
- PLAN.md
**Out of scope**:
- Network imports, archives, media download, or new input formats
- Changing export JSONL schema or Scope/source-reference semantics
- Loading real Telegram credentials in tests
- Git mutations
## Git workflow
- Read-only git inspection only; no stage, commit, push, branch, reset, or worktree.
## Steps
### Step 1: Add the reviewed streaming parser dependency
Add exactly ijson==3.5.1 to project dependencies and regenerate uv.lock with the authorized uv executable. Review the lock diff for only the expected package/backend dependencies. Run frozen sync afterward. If this exact version is unavailable for Python 3.12 on supported platforms, stop and report rather than substituting an unreviewed parser.
**Verify**: <code>uv lock --check</code> and frozen sync pass; pyproject uses an exact pin.
### Step 2: Stream JSONL validation
Replace load_jsonl_import list construction with an iterator yielding JsonlImportItem plus line number. Preserve current precise validation messages and reject an entirely empty file. Consume the iterator in bounded batches, never retaining prior batches.
**Verify**: monkeypatch or fixture proves a later malformed line reports its original line number and earlier committed-batch behavior matches the documented atomicity choice.
### Step 3: Stream supported Telegram JSON shapes
Use ijson's file iterator APIs to support Telegram Desktop messages arrays and Bot API update-dump arrays without path.read_text/json.loads. Preserve existing row normalization and supported attachment/text behavior. Detect shape before writing business data; if the format is neither supported shape, return the existing error without partial import.
**Verify**: tests monkeypatch Path.read_text to fail and both supported fixtures still import.
### Step 4: Batch embedding and short writes
Choose a documented batch size bounded independently from summary_batch_size. Normalize one batch, call embedding_provider.embed with all batch texts outside transactions per plan 008, then write messages/knowledge/embeddings in one short session_scope using idempotent repositories. Record imported/skipped/error counts and audit metadata without file content. Define partial-failure semantics explicitly: either per-batch committed with an audit checkpoint or all-or-nothing via a staging table; do not leave it implicit.
**Verify**: a 1,000-row fixture results in provider call count equal to ceiling(rows/batch_size), not one call per row.
### Step 5: Add keyset/batched export repositories
Add KnowledgeRepository iteration by primary-key cursor and batch metadata/source-reference hydration by scope. Produce JSONL lines incrementally with stable item order and identical fields. For scoped Markdown, stream generated lines; for global canonical Markdown, stream the existing file rather than reading it fully.
**Verify**: streamed output is byte-equivalent to current small-fixture output.
### Step 6: Stream CLI files and Telegram chunks
Expose an iterator/async iterator of MemoryExportChunk values. CLI writes chunks incrementally to out or stdout. Telegram handlers send each bounded document chunk as it is produced instead of building a list containing the complete payload. Preserve the below-50-MB safety margin, deterministic names, and safe_answer_document behavior.
**Verify**: a large synthetic export never hands a chunk over the configured byte limit and does not construct one full encoded payload in tests.
### Step 7: Add limits, regressions, and observability
Add explicit maximum row/file safeguards where streaming alone cannot protect malformed inputs. Cover duplicates across batches, provider failure on a middle batch, line numbers, supported Telegram shapes, export ordering, chunk naming, Unicode boundaries, and audit counts. Tests remain offline.
**Verify**: targeted transfer tests pass and memory use does not scale as one complete duplicate payload in the tested code path.
### Step 8: Document and update PLAN
Document batch size, partial-failure/checkpoint behavior, file limits, provider cost, sensitive export handling, and CLI/Telegram output. Update PLAN.md dependency, tests, operations, risks, version/date/changelog.
**Verify**: <code>rg -n "stream|batch|partial|ijson|file limit" docs/commands.md docs/privacy.md docs/operations.md PLAN.md</code> → contract is explicit.
### Step 9: Run all gates
Run lock validation, targeted tests, format, lint, ty, and full tests.
**Verify**: all exit 0 and only in-scope files are modified.
## Test plan
- JSONL parser streams and preserves exact line errors.
- Telegram Desktop and Bot API shapes parse without read_text.
- Embedding calls are batched.
- Duplicate/idempotency behavior crosses batch boundaries safely.
- Middle-batch failure follows documented checkpoint semantics.
- JSONL field schema/order and Markdown meaning remain compatible.
- Telegram chunks respect byte limit and Unicode boundaries.
## Done criteria
- [x] Supported imports no longer materialize complete row lists/files.
- [x] Embeddings are batched outside write transactions.
- [x] Exports use keyset iteration and batched hydration.
- [x] CLI and Telegram consume bounded chunks incrementally.
- [x] Dependency is exactly pinned and lock remains frozen afterward.
- [x] Privacy/operations/commands docs and PLAN are current.
- [x] All gates pass.
## STOP conditions
- ijson==3.5.1 is unavailable or incompatible on a supported platform.
- Supported Telegram input shapes cannot be detected before writes without buffering the full file.
- Partial-import semantics cannot be made auditable and retry-safe.
- Streaming would change the public JSONL schema or Scope/source mapping.
## Maintenance notes
- New bulk formats must expose iterators and explicit checkpoint semantics from their first implementation.
- Reviewers should inspect memory shape and provider-call counts, not only final output equality.
