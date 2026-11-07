# Live Acceptance (2026-11-07)

This execution follows the owner's request to resolve the delegated-admin policy discrepancy, finish remaining plan work and launch the live topology for BotFather/client testing. Source baseline is committed `60d9c55cdd791712fb79fab502ad4e4bb4f122d1`, the following policy/build/CI/test/documentation changes are uncommitted. Plans 001–020 remain implemented (Plan 016 deliberately retired), Plan 021 remains PARTIAL because its full real-client/provider/release matrix is not complete. This report supersedes the earlier November review's statement that Docker was unavailable, not its historical test evidence.

## Implementation Corrections

- GET, POST and DELETE `/api/control/v1/chats/{chat_id}/admins` require bot-admin authority and advertise `bot_admin` in OpenAPI. Native/delegated chat admins retain their own chat/topic settings and content access but cannot list, create or revoke grants. The Chats page skips delegation requests and hides its delegation controls for scoped users, the existing revocation regression now uses a separate owner session
- A real production image build reproduced the standalone pnpm installer/cache collision, the cache mount at `/pnpm/store` hid the installed CLI. The installer now selects `11.28.11` consistently with `packageManager`, and dependencies use the separate `/var/cache/pnpm` cache/store. This follows pnpm's [specific-version installation contract](https://pnpm.io/installation#installing-a-specific-version) and [separate-cache Docker guidance](https://pnpm.io/docker#minimizing-docker-image-size-and-build-time)
- The missing `uv lock --check` CI step is restored, CI remains manual-only, coverage remains deliberately retired, latest uv and Node major 26 policies are unchanged

The new API tests first failed with delegated/native actors receiving 200 instead of 403, the contract test failed on `chat_admin` instead of `bot_admin`, and the scoped-page test failed because its unconditional delegation fetch prevented settings loading. The new build-cache guard and existing installer/CI guards also failed before their respective fixes. No transaction, schema or provider failover policy was changed.

## Current Deterministic Verification

| Gate | Observed result |
| --- | --- |
| Full Python suite (existing CPython 3.14.7 development environment) | 449 passed, 1 credential-gated Telegram skip, 0 xfails in 151.04 seconds |
| Focused policy/API contract suites | 30 passed, including five roles across browser and Mini App sessions |
| Deployment artifact regressions | 12 passed |
| Static/backend contract | Whole-project Ruff format/lint, `ty`, `uv lock --check` and explicit OpenAPI snapshot check passed |
| Frontend | Format, type-aware lint, Nuxt typecheck, 64 Vitest cases in 15 files and OpenAPI type generation/check passed |
| Production browser build | Fresh ordinary build plus 44 desktop/mobile Playwright cases passed with 2 workers and retries disabled in 26.9 seconds |
| SSR and generated output | Public/protected nonce-aware production SSR smoke passed, generated secret scan passed over 275 text assets |
| Independent review | Fresh read-only review found no actionable correctness/security issue in the final code/config/test diff, it did not independently rerun these gates or review this later report |

The first broader Python run on the committed deployment guards reported 446 passes, 1 skip and two guard failures (missing pnpm selection and missing CI lock-freshness command). The final 449-pass result above supersedes those failures after correction. The new source was not rerun under Python 3.12's full pytest suite, the real production image runs Python 3.12 and passed the runtime/recovery checks below. The previous maintenance report's separate 3.12 suite result remains evidence only for its earlier snapshot. AnyIO's deprecated `CallSpec2` import warning remains unsuppressed, upstream build-timing/color warnings and the previously recorded TypeScript peer-metadata qualification are not live failures.

## Running Test Topology

The Compose project is `pzp-phase10-live`, built from `docker-compose.yml` plus ignored `data/phase10-acceptance/docker-compose.live.yml`, interpolated with ignored `data/phase10-acceptance/compose-live.env`. The bot mounts only `data/phase10-acceptance/live-data` at `/app/data`, the main `data/bot.db` is not used. Caddy's published test listener is restricted to `127.0.0.1:18080`, cloudflared terminates public TLS and forwards there. The public origin is `https://liked-museum-theft-pct.trycloudflare.com`, the callback is `/auth/telegram/callback`, and the webhook is `/telegram/webhook` on that origin. The root deployment secret file was not edited.

One hidden cloudflared process (PID 10132 at this checkpoint) owns the Quick Tunnel, with its log at `data/phase10-acceptance/cloudflared-20261107-150442.stderr.log`. It was kept alive across all container replacements. Quick Tunnel URLs are process-bound test endpoints, not a durable production hostname, stopping that process can require another BotFather update.

| Service | Exact observed image and runtime |
| --- | --- |
| Bot | `pzp-phase10-live-bot:latest`, image `sha256:562119ce7df2501db8d7dd9191c3eae85bd3278d4e4534293835669466dad9a9`, declared user `pzp`, actual UID 100, writable isolated data mount |
| Frontend | `pzp-phase10-live-frontend:latest`, image `sha256:6b5a21c326b1297cdf291930ac20b87cce052aa6ba0ea8513bce8246d39c78d8`, declared user `node`, actual UID 1000, Node `v26.13.0`, `NODE_ENV=production` |
| Proxy | Floating `caddy:2-alpine`, pulled image `sha256:78bd35025c7ba85961dd149ae670315129954e77abe2383ac47c89106b35ade9`, observed Caddy `2.11.9` |

The bot's effective live selection is `openai_compatible`, `gpt-5.6-terra`, `medium` reasoning and `text-embedding-3-small`. Development UID is explicitly cleared, production dev-auth paths are unavailable. Doctor reported configured OIDC, master-key map, database sessions, pool service, supervised alert worker, database and Markdown store as ready without a paid provider call.

## Deployed Observations

- All three services became healthy, the actual isolated schema is at `0017_scope_settings_versions`, Telegram's `getWebhookInfo` confirmed the new exact webhook with zero pending updates and no recorded error at the checkpoint
- Public HTTPS `/health`, `/login` and `/providers` returned 200 with no-store/CSP/HSTS policy. Anonymous `/api/control/v1/session` returned JSON 401, unknown `/api/control/*` and `/auth/*` returned backend JSON 404, GET webhook returned JSON 405 and a POST without the secret returned 401. Explicit forwarded HTTP redirected to the same HTTPS origin. OIDC start returned `oauth.telegram.org` with the exact new callback, no code/PKCE/nonce or identity material was recorded
- A temporary sentinel-only server in the same frontend image observed no Cookie, Authorization or X-CSRF-Token after real Caddy forwarding, while its nonsensitive probe marker arrived. The ordinary Nuxt frontend was restored immediately and public routes were rechecked
- The owner updated BotFather and reported successful initial Main Mini App and normal-browser login. After an announced backend-only forced replacement, the owner explicitly confirmed both sessions stayed signed in. Container IDs proved the frontend was unchanged by that backend replacement. This is operator-reported real authentication/session evidence, not a full supported-client/platform matrix
- Separate no-network non-root processes of the built bot image used the deployed master-key injection to persist a synthetic session/CSRF pair, an encrypted non-provider canary and a selected pool credential. The real `pzp-bot db-backup` CLI produced a backup, a fresh process authenticated the synthetic session, validated CSRF, decrypted the canary, retained the selected credential and passed SQLite integrity/schema checks. Missing referenced keys and incorrect key material each rejected decryption. No real provider credential or raw session token was printed, the synthetic fixture is not evidence of real Telegram authentication
- Elapsed-retention rehearsal used disposable copies of unmodified August 26 records observed on November 7, not fabricated dates or a simulated clock. A 30-day raw policy deleted 9 messages and their 9 embeddings while retaining 4 knowledge items, their 4 embeddings and unchanged Markdown. A separate 60-day knowledge policy deleted 4 knowledge items and their 4 embeddings, retained all 9 messages and reconciled Markdown. Original audit and AI-usage identities survived and pruning events were added. The live acceptance memory was not pruned, this proves image execution against real elapsed-age data copies rather than deletion in the live store

The ignored runtime probes, their disposable databases and the image-build/Python-suite logs are under `data/phase10-acceptance`. Exploratory fixture failures (a transient initial SQLite write failure, a wrong canary lease accessor and an overly strict expected audit count before projection creation) were confined to those copies and resolved before the passing observations above, they did not require application changes or deletion from the live store.

## Explicit Holds

| Gate | Current status and next evidence |
| --- | --- |
| TASK-081 runtime | Substantial current acceptance passed above, retain PARTIAL for a separate real polling smoke and the final release/deployment checklist, never poll with the same bot token while leaving its webhook writer running |
| TASK-082 real clients | First Mini App/browser login and backend-replacement session persistence passed by owner report, remaining checks include account switch/logout/revocation, native/delegated/foreign scope boundaries, scoped save/conflict handling, enrollment and lifecycle actions across the actual supported clients |
| TASK-083 providers/DM | Deferred explicitly by the owner until disposable credentials are available. Both fixed-public-text chat and embedding probes returned classified HTTP 401 with the existing environment key, the owner confirmed it had been revoked a month earlier. The key was not replaced, rotated or disabled by this execution. Real AI-backed Query/summarization, two-key stickiness/failover/recovery/exhaustion and sanitized DM acceptance remain unexecuted |
| TASK-084 final release | Roadmap/docs now reflect this execution, final user-pushed source still needs the manually dispatched hosted CI result and completion of the remaining operator/provider gates. No Git mutation or production release was performed |

## Operator Test Sequence

1. Preserve the now-working origin and existing sessions. First Mini App/browser login and persistence across the backend-only replacement are already accepted, do not recreate the tunnel just to repeat them
2. In both clients, navigate Chats, Bot Settings, Providers, Memory, Stats, Audit and Operations. Existing stored memory should remain visible, protected pages must not expose provider secrets before reveal, and cached diagnostics must not trigger paid AI calls
3. In Chats, change a harmless scoped value such as `retrieval_top_k`, reload and confirm persistence, then restore it. Use two tabs with the same starting version to confirm a stale save reports a conflict rather than silently overwriting the first tab
4. With a second Telegram account, verify native and delegated chat admins see only their active managed chats and can save scoped settings, but have no global settings/provider controls or delegation section. A bot admin can grant/revoke a chat-local delegate, revocation removes access on refresh without requiring unrelated logout. A foreign chat/topic remains denied
5. Test logout and account switching in browser and Mini App, verify the prior identity's navigation/data disappear. For sensitive owner actions, require fresh exact-action Telegram step-up rather than a development bypass. Do not remove the immutable bootstrap owner
6. Use a disposable Telegram group for enrollment, pause/resume and detach. Confirm paused/detached groups retain stored memory but stop processing. Test leave and confirmed forget only on expendable group data, they are distinct operations with explicit confirmation
7. When credentials are ready, create a separate managed `acceptance-pool` profile with two disposable keys (not the revoked environment default), prove one-key healthy stickiness, safely force only the disposable first key to fail, observe one bounded rotation, verify a sanitized bot-admin DM, manually recover, then revoke disposable credentials. Record the exact user/client/provider results, do not treat single-key success as this gate

The primary production rollout and hosted CI remain owner-controlled. The live test stack and tunnel were intentionally left running for these checks.
