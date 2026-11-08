# Planned Scope and Acceptance Closure Implementation Plan
> **For agentic workers:** Use `executing-plans` inline, one fresh-context final review, no Git-managing helpers or worktrees.  
**Goal:** Finish the two written scope gaps and perform the autonomous release acceptance work requested in sections 1 and 2.  
**Architecture:** First extract unchanged route builders/dependencies behind the existing app facade, then add provenance-bound adoption to the existing index service/API/CLI/UI. Final acceptance uses disposable stores and the current isolated live topology.  
**Tech Stack:** Existing Python/FastAPI/SQLAlchemy/pytest/ty, Nuxt/Vue/Vitest/Playwright, uv/pnpm/Docker, no version upgrades.  
**Spec:** [Closure design and authority](../specs/2026-11-07-release-closure-design.md).  
## Constraints and review focus
- Preserve the owner-managed clean baseline, all Git mutations/publication remain forbidden
- Request/auth/step-up/error identity and export cleanup must survive module moves, exact OpenAPI before adoption must not drift
- Adoption cannot derive historical source/provenance evidence from current text, cannot overwrite a verified target or revive forgotten data, and cannot partially commit a failed batch
- Preview/commit must discriminate stale source/vector/configuration, duplicate IDs/targets, real delegated-admin denials and fresh action-specific step-up
- Final test/image/client evidence belongs to its exact source, no inferred success for unavailable credentials/hardware/accounts, preserve tunnel/sessions/live data
## Task 1: Route-family extraction
**Files:** `control_plane/app.py`, new `control_plane/api/{common,auth,providers,alerts,management,query,memory,stats,audit,operations}.py`, embedding route dependencies and architecture regressions.  
**Interface:** Existing `create_control_plane_app`, `create_control_plane_schema_app`, `install_control_plane`, `DevelopmentAuthConfig`, `ControlPlaneAPIError` and used export-stream facade retain identity/behavior, factories reside in route-family modules.  
- [x] Add route ownership/dependency and compatibility regressions, observe RED on monolithic ownership, existing schema/API/auth tests remain the behavioral oracle
- [x] Mechanically move existing bodies and trim imports, remove route-to-app back imports, keep composition/order/middleware unchanged
- [x] Require focused API/auth/development/streaming tests, exact pre-adoption schema check and Ruff/ty to pass
## Task 2: Provenance-bound legacy adoption
**Files:** new `embedding_adoption.py`, `memory/legacy_adoption.py`, `db/repos/legacy_adoption.py` and focused tests, index service/repository integration, `control_plane/embedding_routes.py`, session actions, `embedding_cli.py`, `web/app/pages/embedding-indexes.vue`, API/types/CLI docs and regressions.  
**Interface:** `preview_legacy_adoption(manifest, actor_user_id=None)` returns a safe immutable snapshot digest/count/space summary, `adopt_legacy(manifest, preview_hash, confirmation, provenance_attested, actor_user_id=None)` atomically validates and adopts the bounded explicit mappings.  
- [x] Write RED service cases for successful byte-preserving adoption, missing/incorrect provenance, source/row/config drift, duplicate/conflicting targets and all-or-nothing audit/state
- [x] Implement bounded offline validation under `session_scope`/durable fence, no provider calls or source-text audit
- [x] Write RED owner/scoped-admin/step-up/CSRF/API and CLI/UI confirmation cases, implement the shared operation and synchronized generated contracts
- [x] Require service/control/CLI/UI and schema/static gates to pass, keep unconfirmed real legacy rows untouched
## Task 3: Frozen-candidate qualification
- [x] Run complete Python 3.12 and development-runtime suites in separate fresh external basetemps, final caption-inclusive source passes 689/1 live skip/3 known warnings on both with strict thread-warning handling
- [x] Run complete frontend format/lint/type/unit/browser/API/build/asset/SSR gates (101 unit/52 emulated browsers/284 assets), build final frozen images and prove source/migration/health/non-root/recovery boundaries in disposable stores
- [x] Perform one fresh-context final implementation review, fix the Important browser-continuation finding with RED→GREEN and complete gates, later live-driven corrections receive their own RED→GREEN and complete qualification without an invented re-review
## Task 4: Authorized acceptance deployment and live checks
- [x] Back up and identify the existing isolated stack, preserve its tunnel/origin/secrets/session/data and replace only necessary images, final deployed Python tree matches all 122 source files
- [x] Check actual Desktop rich rendering/long chunks/private Stop/topic progress/menu cleanup and record final-image versus native-proof boundaries, real Web/Mobile clients remain unexecuted external holds
- [x] Check owner browser settings/two-tab/conflict/reset, real action-specific owner verification and approved disposable enrollment/pause/resume/detach/reenroll/leave/confirmed forget, final original-group business rows and browser-session records are preserved
- [ ] PARTIAL: Additional account/role/logout/revocation and real Web/Mobile checks need prepared accounts/clients, no original owner session or unauthorized grant was destroyed to simulate them
- [ ] PARTIAL: Pinned pretrained embedded and loopback local-server ordinary/SSE acceptance passed within the 1 GB artifact cap, two-disposable-key/admin-DM and DeepSeek remain explicitly owner-deferred, production sizing is not established
- [x] Prove exclusive polling through the explicitly approved 158-second webhook pause/sole-poller/restore, no competing writer or dropped updates
- [x] Reconcile PLAN and canonical acceptance docs, report exact completed/partial/unexecuted gates, no publishing or Git mutations
Task 4 is PARTIAL overall because the external account/client/credential/sizing/owner-release holds above remain. This is not a claim that all v1 or Plan 021 Phase 10 acceptance is complete, see [the final qualified report](../../release-closure-acceptance-2026-11-08.md).  
## Execution record
The ignored ledger is `data/release-closure-20261107/progress.md`. Scripts/proofs for this pass stay in that directory, pytest basetemps stay outside the repository. Retain recovery artifacts, no cleanup may remove acceptance backups or other tasks' files.  
