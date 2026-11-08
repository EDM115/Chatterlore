# Release checklist

Use this checklist to qualify a specific source snapshot, its images and its target deployment before tagging or publishing. Record the snapshot, resolved tool versions, schema revision, image digests, commands/results, live client/provider environment and any accepted limitations. Re-run affected checks after changes, earlier test counts or another deployment's success do not qualify a new artifact.

## Source and documentation

- [ ] Review the final diff and package/artifact version metadata, follow the intended release's versioning policy
- [ ] Confirm [setup](setup.md), [configuration](configuration.md), [commands](commands.md), [concepts](concepts.md), [privacy](privacy.md), [deployment](deployment.md), [operations](operations.md) and [secrets](secrets.md) describe the shipped behavior
- [ ] Check examples, local links and environment references, keep real credentials/data out of tracked files and release attachments
- [ ] Record the Alembic head and any upgrade, reindex, availability or operator action required

## Local quality gates

Run from the repository root:

```powershell
uv sync --frozen --extra dev --extra local
uv run ruff format --check .
uv run ruff check .
uv run ty check
uv run pytest -m "not telegram_live"
uv run chatterlore --help
uv run chatterlore doctor
```

- [ ] Complete Python 3.12 production-baseline checks, inspect skips/warnings and exercise optional local packages when supported
- [ ] Check a fresh disposable schema and a disposable prior-release backup with `alembic upgrade head` and `alembic check`, preserve data/settings/permissions
- [ ] Run local `uv lock --check` if validating dependency metadata freshness, CI itself uses frozen sync without that separate command
- [ ] From `web/`, run frozen installation, runtime/format/lint/type/unit/API checks, production build, generated-asset scan, SSR smoke and Playwright as listed in [testing](testing.md)
- [ ] Verify generated OpenAPI/client declarations match the backend and keep private data out of SSR HTML, hydration and built assets

Use fake providers and disposable paths for offline command checks. `doctor` does not prove real authentication or inference, actual provider acceptance is separate.

## Images, ingress and recovery

- [ ] Build both images from the reviewed source, record image digests and resolved installer/base-image versions
- [ ] Run offline backend image `doctor` and migration with disposable paths
- [ ] Run `docker compose config --quiet`, confirm only ingress ports are public and secrets go only to the backend
- [ ] Back up the current database before any new-image application command, preserve matching secrets/keys and rollback images
- [ ] Start the configured disposable HTTPS/OIDC/webhook stack and check all services plus public `/`, `/login` and `/health`
- [ ] Check reserved backend paths never return frontend HTML, missing/wrong webhook secret fails, production development-auth routes are absent and HTTP redirects to HTTPS
- [ ] Check private/no-store documents, HSTS/CSP/nonces and frontend credential-header stripping through the actual proxy
- [ ] Replace the frontend independently, restart the backend and verify browser/Mini App session continuity, grants and webhook delivery
- [ ] Rehearse key-injected database restore on isolated native deployment storage, verify integrity, schema, synthetic encrypted credentials and fail-closed behavior with a missing/wrong key
- [ ] Check non-root bind-mount permissions and SQLite sidecar creation, a Windows-mounted rehearsal can differ from native Linux storage
- [ ] Coordinate any polling handoff with an exclusive update consumer, preserve queued updates and restore the webhook URL/update subscription afterward

Do not label acknowledged Ask tasks as durable, process loss can interrupt them without automatic paid replay. Check [operations](operations.md#backup-upgrade-and-restore) before restore or rollback.

## Telegram and identity acceptance

Use disposable groups/topics, synthetic markers and prepared test accounts. Record the actual client, account/role and observed result for each scenario.

- [ ] Default command menu is empty, Main Mini App remains reachable and manually typed `/help` works
- [ ] Real Desktop, Telegram Web and supported mobile clients render headings/tables/code/Unicode and preserve long final content/source references
- [ ] Private Ask shows progress, persists its final reply, native Stop cancels generation and leaves no stale draft/Generating state
- [ ] Group and forum-topic progress finalizes the same message, rate limits and ambiguous transport failures avoid duplicate replies
- [ ] General uses topic 1, sibling-topic questions cannot retrieve a private marker, explicit whole-chat scope includes permitted aggregate/topic memory
- [ ] Enable/disable learning, inspect `/learn_status`, `/memory_status` and skip diagnostics, distinguish learning denial from stored raw-message retention
- [ ] Exercise review approval/rejection, note/item maintenance, export parts and confirmed settings/forget on disposable content
- [ ] Validate browser OIDC and signed Mini App login, session persistence, account switch, expiry, browser logout and owner/session revocation
- [ ] Check bot-admin, native/delegated chat-admin and foreign/removed actors across read/write/settings/provider/audit/export paths, only bot admins manage delegated grants
- [ ] Exercise requester-bound prepared Mini App and browser-DM group selection, expired/foreign selection must fail
- [ ] Check pause/resume/detach/reenroll/leave/confirmed forget differences, exact action verification and stale-version conflicts
- [ ] Verify two-tab settings conflicts, reset-to-inherited behavior and restored test settings
- [ ] Confirm forgetting/review rejection/retention leave no removed content in daily/canonical Markdown, retain only the intended audit and backup material
- [ ] Rehearse elapsed raw-message and knowledge retention on isolated data with actual aged records, verify vectors/projections and audit retention separately

The credential-gated `telegram_live` pytest checks bot identity only. Browser emulation, mocked identity exchanges and one owner's Desktop session do not cover this complete matrix.

## Providers, indexes and capacity

- [ ] Test each supported remote vendor/dialect with explicitly authorized disposable credentials, including DeepSeek when that integration is part of the release
- [ ] Use two disposable keys to verify sticky selection, classified failover, recovery and exhaustion, receive sanitized admin DMs and check unreachable-recipient/retry behavior
- [ ] Verify create/replace/reveal/delete step-up, masking/lifecycle clearing, environment-key non-revealability and master-key rotation/recovery
- [ ] Test independent chat/summary/embedding routes and provider-specific payload/reasoning behavior, no key may cross endpoints or reach a no-auth local server
- [ ] Prepare approved pinned local artifacts within the deployment byte cap, distinguish cached readiness, successful embedded inference and local HTTP/SSE operation
- [ ] Measure representative model quality, sustained/concurrent load, memory/disk needs, latency and CPU/GPU suitability on the target host
- [ ] Rebuild legacy-unverified vectors or adopt only with retained original provenance, verify exact-space/current-source coverage before activation
- [ ] Exercise resumable job failure/cancellation/restart, current-version activation and coverage-checked rollback, preserve source text and old vectors

A tiny-model functional check does not establish larger-model quality, GPU behavior or production capacity. Equal vector dimensions and model names do not establish historical provenance.

## Outstanding qualification

The project still requires release-owner decisions and evidence for the following areas. Keep a gate open until you run it for the intended artifact or explicitly narrow the published support claim.

| Open gate | Required evidence |
| --- | --- |
| Two-key failover and admin DMs | Real disposable-key failover/recovery/exhaustion and sanitized delivery |
| DeepSeek | Credential-dependent real request acceptance |
| Clients, accounts and permissions | Real Web/mobile, account switching, foreign/delegated actors, current-source logout and revocation with prepared accounts/sessions |
| Production sizing | Sustained load and deployment-specific model quality, CPU/GPU/RAM/disk measurements |
| Publication and hosted CI | Owner-reviewed/published snapshot, manually dispatched CI result for it, release tagging and production rollout decision |

Legacy data without original embedding provenance remains unverified until rebuilt, existing vectors are preserved. Mark each gate against the intended release artifact, a passing isolated check does not qualify another client, provider or deployment.

## Publication and release notes

- [ ] After the owner publishes the reviewed snapshot, manually dispatch `.github/workflows/ci.yml` and record its exact result, push/PR events do not trigger it
- [ ] Resolve failures and rerun affected gates before tagging, preserve the owner's control of Git/release actions
- [ ] Publish notes with package/schema versions, configuration/command/privacy changes, upgrade/reindex actions, image digests and validated deployment/client/provider scope
- [ ] List limitations and explicit waivers with their impact, identify rollback/recovery requirements and the matching backup/key policy
- [ ] Tag and deploy only after the release owner accepts the evidence and remaining limitations
