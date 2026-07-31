# Nuxt SSR And Caddy Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the static Nuxt SPA deployment with a secure SSR Node service behind a dedicated Caddy ingress while keeping FastAPI private, authoritative and independently deployable.

**Architecture:** Caddy is the only public service and reserves API, authentication, Telegram webhook and health paths for the existing FastAPI webhook runtime on port 8080. All other paths reach Nuxt SSR on port 3000, with credential headers stripped before the frontend upstream and protected content withheld until client-side session bootstrap succeeds.

**Tech Stack:** Nuxt 4.5.1, Vue 3.5.40, Nuxt UI 4.10.0, Node.js 24.18.0, pnpm 11.18.0, Caddy 2.11.4, Docker Compose, FastAPI 0.140.13, pytest, Vitest and Playwright.

## Global Constraints

- Do not implement any Plan 021 Phase 5 provider-pool behavior.
- Do not run Docker commands while the local daemon is unavailable; preserve an explicit deferred runtime gate.
- Use raw `pnpm typecheck` because RTK rewrites that package script incorrectly, and use `pnpm <bin>` or declared package scripts for frontend binaries.
- Use uv or the repository virtual environment for every Python command; retry uv commands outside the sandbox when its shared cache is denied.
- Keep frontend requests same-origin at `/api/control/v1`; do not add CORS or a Nitro backend-for-frontend.
- Keep all secrets in the bot container; Caddy receives only the public origin and Nuxt receives no backend secret configuration.
- Do not stage, commit, branch, reset or create a worktree. The repository owner owns every additive Git operation.
- Preserve Markdown one-line prose and update `PLAN.md` plus Plan 021 after the implementation changes actual project state.

---

### Task 1: Enforce The SSR Authentication Boundary

**Files:**
- Create: `web/app/components/ProtectedPageGate.vue`
- Modify: `web/app/pages/index.vue`
- Modify: `web/app/middleware/auth.ts`
- Modify: `web/app/composables/useControlSession.ts`
- Modify: `web/nuxt.config.ts`
- Delete: `web/app/spa-loading-template.html`
- Test: `web/e2e/shell.spec.ts`

**Interfaces:**
- Consumes: `useControlSession()` and the existing `auth` page middleware.
- Produces: `ProtectedPageGate`, which renders a neutral server-safe fallback and releases its slot only for an authenticated hydrated client.

- [x] **Step 1: Write the failing JavaScript-disabled protected direct-route test**

```ts
test.use({ javaScriptEnabled: false });
await expect(page.getByTestId("auth-pending")).toContainText("Checking Telegram access");
await expect(page.getByRole("heading", { name: "Memory operations at a glance" })).toHaveCount(0);
```

- [x] **Step 2: Run the focused production Playwright route and verify the old SPA loading document fails the protected-render assertion**
- [x] **Step 3: Implement `ProtectedPageGate`, add server guards to middleware and session bootstrap, enable SSR, remove prerendering and remove the obsolete SPA loading template**

```ts
if (import.meta.server) return;
```

- [x] **Step 4: Wrap the protected overview body in `ProtectedPageGate` and run the focused test until green**
- [x] **Step 5: Run all Vitest tests to catch session and shell regressions**

### Task 2: Add SSR Security Headers And Production Behavior Verification

**Files:**
- Create: `web/server/middleware/security-headers.ts`
- Create: `web/scripts/smoke-ssr.mjs`
- Modify: `web/package.json`
- Test: `web/e2e/shell.spec.ts`
- Modify: `web/playwright.config.ts`

**Interfaces:**
- Consumes: Nuxt's Node server output at `.output/server/index.mjs` and the public asset directory `.output/public`.
- Produces: `pnpm build`, `pnpm start`, `pnpm smoke:ssr` and security headers on SSR documents.

- [x] **Step 1: Add failing JavaScript-disabled direct-login/protected-route Playwright coverage and retain the existing `.output/public` browser-asset scanner**
- [x] **Step 2: Run the focused production Playwright test and confirm the missing SSR security headers fail**
- [x] **Step 3: Add the Nitro response-header middleware with private no-store HTML policy, CSP, HSTS, referrer, permissions and MIME-sniffing protection while leaving immutable asset caching to route rules and ingress**
- [x] **Step 4: Replace the static-generation package surface with production build/start/smoke scripts**

```json
{
  "build": "nuxt build",
  "start": "node .output/server/index.mjs",
  "smoke:ssr": "node ./scripts/smoke-ssr.mjs"
}
```

- [x] **Step 5: Implement the smoke runner with `spawn(process.execPath, [entrypoint])`, bounded readiness polling, safe process termination and literal assertions for login HTML, protected fallback, absent privileged content, no old SPA loading copy, security headers and absent sentinel secrets**
- [x] **Step 6: Run `pnpm build`, `pnpm scan:generated`, `pnpm smoke:ssr`, focused Playwright and the full frontend unit suite**

### Task 3: Define The Split Images And Caddy Ingress

**Files:**
- Create: `web/Dockerfile`
- Create: `web/.dockerignore`
- Create: `Caddyfile`
- Modify: `docker-compose.yml`
- Test: `tests/test_deployment_artifacts.py`

**Interfaces:**
- Consumes: the bot image's `pzp-bot webhook` command, internal FastAPI port 8080 and Nuxt production port 3000.
- Produces: independent `bot`, `frontend` and `proxy` services with only Caddy publishing host ports.

- [x] **Step 1: Extend deployment tests to parse Compose with `yaml.safe_load` and assert three independent services, webhook mode, internal-only upstream ports, a secret-free frontend environment, exact Caddy pin and Caddy-only host publishing**
- [x] **Step 2: Run `uv run pytest tests/test_deployment_artifacts.py` with a fresh external base temp and confirm the missing service/artifact assertions fail**
- [x] **Step 3: Add the exact Node multi-stage frontend Dockerfile**

```dockerfile
FROM node:24.18.0-bookworm-slim AS dependencies
RUN corepack enable && corepack prepare pnpm@11.18.0 --activate
FROM dependencies AS build
RUN pnpm build
FROM node:24.18.0-bookworm-slim AS runtime
COPY --from=build --chown=node:node /app/.output ./.output
USER node
CMD ["node", ".output/server/index.mjs"]
```

- [x] **Step 4: Add the focused frontend Docker context exclusions and Caddyfile reserved-route contract, including removal of cookie, authorization and CSRF headers from frontend requests**
- [x] **Step 5: Replace the one-service polling Compose file with the independently buildable three-service webhook topology, exact Caddy image, internal health checks, Caddy state volumes and no cross-service restart propagation**
- [x] **Step 6: Run the focused Python artifact tests until green without invoking Docker**

### Task 4: Move CI From Static Generation To SSR Gates

**Files:**
- Modify: `.github/workflows/ci.yml`
- Modify: `tests/test_frontend_workspace.py`

**Interfaces:**
- Consumes: the package scripts from Task 2 and Dockerfiles from Task 3.
- Produces: hosted frontend build/smoke coverage and independent backend/frontend image builds.

- [x] **Step 1: Update the frontend-workspace test to require `pnpm build`, `pnpm scan:generated` and `pnpm smoke:ssr`, and to reject the obsolete `pnpm generate` gate**
- [x] **Step 2: Run the focused Python test and verify it fails against the old CI workflow**
- [x] **Step 3: Change the frontend job to build the Node server, scan browser assets and run the production SSR smoke**
- [x] **Step 4: Extend the Docker job to build the Python bot image and the frontend image independently while preserving the offline bot doctor check**
- [x] **Step 5: Run the focused frontend-workspace and deployment-artifact tests until green**

### Task 5: Reconcile Human Documentation And Roadmap State

**Files:**
- Modify: `README.md`
- Modify: `docs/architecture.md`
- Modify: `docs/deployment.md`
- Modify: `docs/setup.md`
- Modify: `docs/privacy.md`
- Modify: `docs/secrets.md`
- Modify: `docs/testing.md`
- Modify: `docs/release.md`
- Modify: `plans/021-provider-key-pools-and-telegram-control-plane.md`
- Modify: `PLAN.md`

**Interfaces:**
- Consumes: the verified behavior and exact deferred checks from Tasks 1 through 4.
- Produces: one consistent operator story and exact `DONE`, `PARTIAL` and `TODO` roadmap claims.

- [x] **Step 1: Replace static/client-only architecture prose with the Caddy/Nuxt SSR/FastAPI trust boundary and independent image lifecycle**
- [x] **Step 2: Document production webhook requirements, public-origin configuration, internal ports, independent deployment commands, polling as a local/manual mode and the no-secret frontend environment**
- [x] **Step 3: Replace static-generation test/release commands with build, asset scan, SSR smoke, Playwright and eventual Compose proxy acceptance**
- [x] **Step 4: Add an approved SSR transition checkpoint between Plan 021 Phases 4 and 5, update superseded constraints and record Docker runtime acceptance as pending rather than complete**
- [x] **Step 5: Update `PLAN.md` known partials, gates, runbook and changelog with the exact fresh test evidence from Task 6**

### Task 6: Run The Complete Non-Docker Verification Checkpoint

**Files:**
- Review: every file changed by Tasks 1 through 5.

**Interfaces:**
- Consumes: all implemented SSR/deployment artifacts.
- Produces: evidence-backed checkpoint and exact remaining Docker acceptance list.

- [x] **Step 1: Run `pnpm format:check`, `pnpm lint`, raw `pnpm typecheck`, `pnpm test`, `pnpm openapi:check`, `pnpm build`, `pnpm scan:generated` and `pnpm smoke:ssr`**
- [x] **Step 2: Run `pnpm test:e2e` using the installed Chromium and confirm desktop/mobile plus JavaScript-disabled SSR behavior**
- [x] **Step 3: Run `uv run ruff format --check .`, `uv run ruff check .`, `uv run ty check` and a fresh full `uv run pytest` with an external base temp**
- [x] **Step 4: Inspect the read-only Git diff, scan the implementation plan for unchecked or inaccurate claims, update the roadmap with final counts and leave every Docker runtime check visibly deferred**
- [x] **Step 5: Stop before Plan 021 Phase 5 and report the exact implementation, verification and unexecuted Docker boundaries without staging or committing**
