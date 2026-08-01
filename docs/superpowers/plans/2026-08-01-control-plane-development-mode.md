# Combined Control-Plane Development Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `pnpm dev` start a loopback Nuxt SSR development server and a dedicated FastAPI control-plane development runtime with backend-selected mock Telegram identity, optional deliberate real-webhook connectivity, and production behavior that remains fail-closed.

**Architecture:** A new Python development-runtime module will validate command-scoped environment values, construct a validated-in-production-plus-explicit-development settings overlay, provision a stable ignored development vault key when necessary, build the application through `build_context(Settings)`, and serve a dedicated FastAPI app. The existing control-plane installer will accept an internal development-auth descriptor only when called by that module; the normal schema app and webhook app will never supply it. A dependency-free Node coordinator will own both child processes, while Nuxt development builds add complete reserved-route proxies, automatic mock login after an exact 401, and exact-action development step-up.

**Tech Stack:** Python 3.12, uv, Typer, FastAPI/Starlette, SQLAlchemy/Alembic, aiogram Bot API 10.2 support, Pydantic Settings, Nuxt 4.5.1 SSR, Vue 3.5.40, TypeScript 6.0.3, Node.js 24.18.0 from pnpm 11.18.0, Vitest, Playwright, Ruff and ty.

**Implementation status:** Implemented and locally verified on 2026-08-01. Optional real connected-Telegram acceptance and production Docker/Caddy/OIDC deployment remain separate Plan 021 Gate E work and are not claimed by this development-mode checkpoint.

## Global Constraints

- Use `uv run` for Python, project binaries and Python verification; use raw `pnpm` for package scripts and `pnpm <bin>` for local binaries.
- Preserve the normal `Settings.validate_control_plane_bootstrap` production contract; localhost HTTP and mock identity are available only through `pzp-bot control-plane-dev`.
- Build the Python application through `build_context(Settings)` and mutate only the development context's in-memory `AdminService.bot_admin_ids` to grant the configured UID bot-admin capability.
- Keep all Telegram calls disabled when `WEBHOOK_URL` is empty, even if `TELEGRAM_BOT_TOKEN` is present.
- Enable real Telegram registration, webhook receipt and administrator-DM delivery only when `WEBHOOK_URL`, `TELEGRAM_BOT_TOKEN` and a non-placeholder `WEBHOOK_SECRET` form a complete HTTPS configuration.
- Never send backend secrets or the configured development UID to Nuxt; never accept a UID in development-auth request data.
- Keep both development-auth routes absent from the normal webhook app, schema app, production OpenAPI artifact and production Nuxt output.
- Keep production browser cookies `Secure`; loopback HTTP development cookies use `HttpOnly`, `SameSite=Lax` and `Secure=false`.
- Add no process-manager dependency; use Node's built-in child-process and signal APIs plus the operating system's fixed PowerShell 7/`taskkill` process-tree boundary on Windows.
- Preserve the existing dirty worktree. Do not stage, commit, branch, reset, clean or create a worktree.
- Write tests before production code, run each focused test to observe the expected failure, implement the smallest passing behavior, and rerun the focused test before proceeding.

---

### Task 1: Command-scoped development settings and stable vault key

**Files:**
- Create: `src/pzp_ai_bot/control_plane/development.py`
- Create: `tests/test_control_plane_development.py`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: `Settings`, `Settings.model_copy(update=...)`, `Settings.control_plane_master_key_bytes`, and the repository-root process working directory.
- Produces: `ControlPlaneDevelopmentConfig`, `build_control_plane_development_settings(settings, *, environment, key_path) -> tuple[Settings, ControlPlaneDevelopmentConfig]`, and `load_or_create_development_master_key(path) -> str`.

- [x] **Step 1: Write failing configuration-boundary tests**

Add table-driven tests with literal expectations that prove positive integer parsing, rejection of zero/negative/non-integer UIDs, exact acceptance of `http://localhost:3000`, `http://127.0.0.1:3000` and `http://[::1]:3000`, rejection of credentials/path/query/fragment/non-loopback hosts, default port `8000`, and rejection of ports outside `1..65535`.

```python
@pytest.mark.parametrize(
    "origin", ["http://localhost:3000", "http://127.0.0.1:3000", "http://[::1]:3000"]
)
def test_development_settings_accept_only_explicit_loopback_origins(
    tmp_path: Path, origin: str
) -> None:
    settings, config = build_control_plane_development_settings(
        Settings(_env_file=None),
        environment={"CONTROL_PLANE_DEV_USER_ID": "887758628", "CONTROL_PLANE_DEV_ORIGIN": origin},
        key_path=tmp_path / "dev-key",
    )
    assert config.user_id == 887_758_628
    assert config.origin == origin
    assert settings.control_plane_enabled is True
    assert settings.control_plane_public_origin == origin
    assert settings.bot_admin_ids == set()
```

- [x] **Step 2: Run the new configuration tests and verify RED**

Run: `uv run pytest tests/test_control_plane_development.py -q`  
Expected: collection/import failure because `pzp_ai_bot.control_plane.development` does not exist.

- [x] **Step 3: Add the minimal development configuration model and validators**

Implement a frozen slotted configuration dataclass and explicit parsing helpers. Build the overlay only after a normal `Settings` object exists, using `model_copy(update=...)` so the production model validator is neither weakened nor made env-activatable.

```python
@dataclass(frozen=True, slots=True)
class ControlPlaneDevelopmentConfig:
    user_id: int
    origin: str
    api_port: int
    telegram_connected: bool


def build_control_plane_development_settings(
    settings: Settings,
    *,
    environment: Mapping[str, str] | None = None,
    key_path: Path = Path("data/control-plane-dev-master-key"),
) -> tuple[Settings, ControlPlaneDevelopmentConfig]:
    values = os.environ if environment is None else environment
    user_id = _positive_int(
        values.get("CONTROL_PLANE_DEV_USER_ID", ""), "CONTROL_PLANE_DEV_USER_ID"
    )
    origin = _loopback_origin(values.get("CONTROL_PLANE_DEV_ORIGIN", "http://localhost:3000"))
    api_port = _port(values.get("CONTROL_PLANE_DEV_API_PORT", "8000"))
    master_keys, active_key_id = _development_master_key_settings(settings, key_path)
    connected = bool(settings.webhook_url.strip())
    overlay = settings.model_copy(
        update={
            "control_plane_enabled": True,
            "control_plane_public_origin": origin,
            "control_plane_master_keys": master_keys,
            "control_plane_active_master_key_id": active_key_id,
        }
    )
    return overlay, ControlPlaneDevelopmentConfig(user_id, origin, api_port, connected)
```

- [x] **Step 4: Write failing stable-key tests**

Prove the generated value decodes to exactly 32 bytes, survives a second load unchanged, is created exclusively without truncating a competing value, is never used when a complete configured key map is valid, and raises a content-free `ValueError` for empty, malformed, unreadable or wrong-length existing key material.

- [x] **Step 5: Run the stable-key tests and verify RED**

Run: `uv run pytest tests/test_control_plane_development.py -q -k "key or configured"`  
Expected: failure because the key loader and fallback policy are absent.

- [x] **Step 6: Implement atomic stable-key creation and explicit corruption failure**

Use `secrets.token_bytes(32)`, URL-safe or standard base64, exclusive `Path.open("x", encoding="ascii")`, an immediate flush, and a race fallback that rereads the winner. Never log or include material in exceptions. Add `data/control-plane-dev-master-key` explicitly to `.gitignore` even though `data/` is already ignored, documenting the security-sensitive intent.

- [x] **Step 7: Run the complete development-configuration test slice and verify GREEN**

Run: `uv run pytest tests/test_control_plane_development.py -q`  
Expected: all Task 1 tests pass.

### Task 2: Development-only authentication, exact-action step-up and cookie policy

**Files:**
- Modify: `src/pzp_ai_bot/control_plane/app.py`
- Modify: `src/pzp_ai_bot/control_plane/middleware.py`
- Modify: `src/pzp_ai_bot/control_plane/schemas.py`
- Modify: `tests/test_control_plane_development.py`
- Modify: `tests/test_control_plane_api.py`
- Modify: `tests/test_control_plane_contract.py`

**Interfaces:**
- Consumes: `ControlPlaneSessionService.issue`, `authenticate`, `validate_csrf`, `mark_step_up`, `SENSITIVE_ACTIONS`, `TelegramIdentity`, `_audit`, normal authorization resolution and `ControlPlaneRequestGuardMiddleware`.
- Produces: `CONTROL_PLANE_DEV_AUTH_PATH`, `CONTROL_PLANE_DEV_STEP_UP_PATH`, `DevelopmentAuthConfig(telegram_user_id: int)`, `install_control_plane(..., development_auth: DevelopmentAuthConfig | None = None)`, `DevelopmentStepUpRequest(action: str)`, dev login at `/api/control/v1/auth/dev`, and dev step-up at `/api/control/v1/auth/dev/step-up`. These route declarations live in `control_plane/app.py`; `control_plane/development.py` imports them when installing the dedicated app, avoiding an import cycle.

- [x] **Step 1: Write failing API tests for development login**

Construct a normal test context from the development settings overlay, add the configured UID only to `context.admins.bot_admin_ids`, install the development auth descriptor, and prove the POST body is empty, exact loopback origin is mandatory, the response uses the existing session shape, capability is `bot.admin`, CSRF rotates through the real session service, audit data contains no request content, and the cookie lacks `Secure` only for loopback HTTP.

```python
login = client.post("/api/control/v1/auth/dev", headers={"Origin": origin})
assert login.status_code == 200
assert login.json()["user"]["telegram_user_id"] == 887_758_628
assert login.json()["capabilities"] == ["bot.admin"]
assert "HttpOnly" in login.headers["set-cookie"]
assert "SameSite=lax" in login.headers["set-cookie"]
assert "Secure" not in login.headers["set-cookie"]
```

- [x] **Step 2: Run the development-login tests and verify RED**

Run: `uv run pytest tests/test_control_plane_development.py -q -k "login or cookie"`  
Expected: 404 for `/api/control/v1/auth/dev`.

- [x] **Step 3: Parameterize the installer without changing production callers**

Make `_build_auth_router` accept `DevelopmentAuthConfig | None`; add the dev route only when non-`None`. Pass an immutable CSRF-exempt path set into middleware, defaulting to only `/api/control/v1/auth/mini-app`; add the dev login path only in the dedicated install call. Derive the cookie's secure flag from the exact configured origin scheme and reuse it for both `set_cookie` and `delete_cookie`.

```python
public_post_paths = {"/api/control/v1/auth/mini-app"}
if development_auth is not None:
    public_post_paths.add(CONTROL_PLANE_DEV_AUTH_PATH)
app.add_middleware(
    ControlPlaneRequestGuardMiddleware,
    settings=context.settings,
    sessions=context.control_plane.sessions,
    public_post_paths=frozenset(public_post_paths),
)
```

- [x] **Step 4: Implement the backend-selected development login**

Create the identity from `DevelopmentAuthConfig.telegram_user_id` and fixed synthetic fields (`first_name="Development administrator"`, `username="pzp_control_plane_dev"`, no premium/photo). Issue a normal `browser` session, resolve the real actor, emit a content-free `control_plane.auth.success` event with method `development`, replace an existing session cookie, and return `_session_payload_from_issue`.

- [x] **Step 5: Rerun development-login tests and verify GREEN**

Run: `uv run pytest tests/test_control_plane_development.py -q -k "login or cookie"`  
Expected: the login, origin, audit and HTTP-cookie tests pass.

- [x] **Step 6: Write failing exact-action development step-up tests**

Prove the route requires the issued cookie, exact CSRF header and exact origin; rejects missing, generic and unknown actions; rejects a session whose identity differs from the configured UID; marks only one action; permits the corresponding credential reveal; and does not permit a different sensitive action.

- [x] **Step 7: Run the step-up tests and verify RED**

Run: `uv run pytest tests/test_control_plane_development.py -q -k "step_up"`  
Expected: 404 for the development step-up route.

- [x] **Step 8: Implement exact-action development step-up**

Add `DevelopmentStepUpRequest` with a bounded action string. Authenticate through the existing cookie, compare the session Telegram UID to the fixed development UID, require membership in `SENSITIVE_ACTIONS`, call `mark_step_up`, audit `step_up:<action>` with method `development`, and return the existing `StepUpResponse` shape.

- [x] **Step 9: Prove production contract isolation and cookie invariants**

Extend production tests to assert both development routes are absent from `create_control_plane_app`, `create_webhook_app`, `create_control_plane_schema_app().openapi()` and the checked-in OpenAPI path set. Retain the existing assertion that production cookies include `Secure`, and assert production logout deletion uses matching secure attributes.

- [x] **Step 10: Run all focused control-plane tests and verify GREEN**

Run: `uv run pytest tests/test_control_plane_development.py tests/test_control_plane_api.py tests/test_control_plane_contract.py -q`  
Expected: all non-xfailed tests pass and existing strict xfails remain xfailed for unrelated Plan 021 slices.

### Task 3: Dedicated FastAPI runtime and optional real Telegram connectivity

**Files:**
- Modify: `src/pzp_ai_bot/control_plane/development.py`
- Modify: `src/pzp_ai_bot/main.py`
- Modify: `src/pzp_ai_bot/telegram/webhook.py`
- Modify: `tests/test_control_plane_development.py`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_webhook.py`

**Interfaces:**
- Consumes: `build_context`, `install_control_plane`, aiogram `Bot`/`Dispatcher`, `build_dispatcher`, `register_bot_commands`, scheduler start/stop helpers, `require_safe_webhook_secret`, `Update.model_validate` and Uvicorn.
- Produces: `create_control_plane_development_app(context, config, *, bot=None, dispatcher=None) -> FastAPI`, `async run_control_plane_development(settings, *, environment=None) -> None`, CLI command `pzp-bot control-plane-dev`, and `require_https_webhook_url(url) -> None`.

- [x] **Step 1: Write failing offline-runtime tests**

Use a Telegram double whose every method raises if called. With an empty `WEBHOOK_URL` and even a nonempty bot token, prove app creation and lifespan perform zero Telegram calls, `/telegram/webhook` returns 404, `/health` reports both critical tasks healthy, the control-plane development login works, no alert-delivery task starts, and queued alert state remains queryable through the control-plane API.

- [x] **Step 2: Run offline-runtime tests and verify RED**

Run: `uv run pytest tests/test_control_plane_development.py -q -k "offline or health"`  
Expected: failure because the dedicated app factory is absent.

- [x] **Step 3: Implement the offline FastAPI app and lifespan**

Create no Bot and no Dispatcher when `config.telegram_connected` is false. Always start the idle summarizer and runtime-settings refresher; expose the same critical-task `/health` payload used by webhook mode; install the control plane with `DevelopmentAuthConfig`; close the OIDC client and stop every started task in `finally`.

- [x] **Step 4: Write failing connected-runtime validation and lifecycle tests**

Cover missing token, unsafe secret variants and non-HTTPS webhook URL with an injected double that records zero calls before failure. With complete configuration, prove command registration and `set_webhook(url, secret_token=..., drop_pending_updates=False)` occur during lifespan, `/telegram/webhook` enforces the secret, the admin-alert task starts but is not critical health, and an owned bot session closes on shutdown.

- [x] **Step 5: Run connected-runtime tests and verify RED**

Run: `uv run pytest tests/test_control_plane_development.py -q -k "connected or telegram"`  
Expected: connected-mode creation or lifecycle assertions fail because no implementation exists.

- [x] **Step 6: Implement fail-closed connected mode**

Validate all connected settings before constructing or calling a bot. Require `urlsplit(url).scheme == "https"`, a hostname, no credentials and no fragment; reuse the production webhook-secret denylist. Register the dispatcher route only in connected mode, start alert delivery only after a bot exists, and preserve production's provider-result independence from asynchronous alert-delivery failures.

- [x] **Step 7: Add the Typer command and bounded Uvicorn runner**

The synchronous CLI command loads normal cached settings, constructs the overlay/config from `os.environ`, builds context through `build_context`, mutates only `context.admins.bot_admin_ids`, and runs Uvicorn bound to `127.0.0.1:<config.api_port>`. Convert configuration `ValueError`/`RuntimeError` into safe `typer.BadParameter` output without printing key material, tokens or full credential-bearing URLs.

```python
@app.command("control-plane-dev")
def control_plane_dev() -> None:
    """Run the loopback development control plane, optionally with Telegram webhook delivery."""
    try:
        run(run_control_plane_development(get_settings()))
    except (ValueError, RuntimeError) as exc:
        raise typer.BadParameter(str(exc)) from exc
```

- [x] **Step 8: Add CLI tests and preserve production webhook tests**

Invoke `pzp-bot control-plane-dev --help`; monkeypatch the async runner for a success-path command test; prove missing UID yields a nonzero safe message; prove secret/key sentinels never appear. Keep all existing `create_webhook_app` behavior and tests unchanged except extracting a shared HTTPS validator or health-payload helper when that reduces duplication.

- [x] **Step 9: Run runtime, CLI and webhook tests and verify GREEN**

Run: `uv run pytest tests/test_control_plane_development.py tests/test_cli.py tests/test_webhook.py -q`  
Expected: all tests pass.

### Task 4: Dependency-free `pnpm dev` process coordinator

**Files:**
- Create: `web/scripts/dev.mjs`
- Create: `web/test/dev-coordinator.unit.spec.ts`
- Modify: `web/package.json`

**Interfaces:**
- Consumes: Node `spawn`, fixed non-interactive PowerShell 7 wrappers on Windows, direct `uv`/`pnpm` children on Unix, repository root derived from `import.meta.url`, and inherited environment.
- Produces: `developmentProcessSpecs(platform)`, `coordinateDevelopmentProcesses(options)`, `pnpm dev` combined mode and `pnpm dev:web` frontend-only mode.

- [x] **Step 1: Write failing pure process-spec tests**

Prove the backend specification is `uv run pzp-bot control-plane-dev` with repository-root `cwd`, the frontend specification is `pnpm dev:web` with `web/` `cwd`, Windows uses persistent fixed PowerShell 7 wrapper roots, Unix uses direct binaries, no mutable shell input is accepted, and both inherit the caller environment without adding secrets.

- [x] **Step 2: Run coordinator tests and verify RED**

Run from `web/`: `pnpm test -- dev-coordinator.unit.spec.ts`  
Expected: import failure because `scripts/dev.mjs` does not exist.

- [x] **Step 3: Implement process-spec construction and package scripts**

Change `dev` to `node ./scripts/dev.mjs`, add `dev:web` as `nuxt dev`, and export pure spec construction from the script. Derive paths with `fileURLToPath(import.meta.url)` and `resolve`; use `windowsHide: true`, `shell: false`, `stdio: ["inherit", "pipe", "pipe"]`.

- [x] **Step 4: Write failing lifecycle tests with child-process doubles**

Use real `EventEmitter`-based child doubles and controlled stdout/stderr streams. Prove output receives `[backend]`/`[web]` prefixes, an unexpected nonzero exit terminates the sibling and rejects with that code, a zero exit while the sibling is live is still treated as unexpected failure, `SIGINT`/`SIGTERM` terminate both children, the coordinator resolves after both exit, and a forced-kill timeout cannot leave a child marked running.

- [x] **Step 5: Run lifecycle tests and verify RED**

Run from `web/`: `pnpm test -- dev-coordinator.unit.spec.ts`  
Expected: lifecycle assertions fail because orchestration is absent.

- [x] **Step 6: Implement lifecycle coordination and executable main guard**

Spawn both specs, line-prefix output without buffering unbounded data, install temporary signal handlers, initiate sibling shutdown exactly once, wait up to five seconds, remove listeners in `finally`, set `process.exitCode` from the first failure, and execute only when `pathToFileURL(resolve(process.argv[1])).href === import.meta.url`. Use ordinary signals plus `SIGKILL` escalation on Unix; terminate the complete stable wrapper tree through fixed-argument `taskkill /T /F` on Windows and fall back to direct child termination only if `taskkill` cannot start.

- [x] **Step 7: Run coordinator tests and package metadata checks and verify GREEN**

Run from `web/`: `pnpm test -- dev-coordinator.unit.spec.ts`  
Run from `web/`: `pnpm runtime:check`  
Expected: tests pass and runtime reports `v24.18.0`.

### Task 5: Nuxt development proxies, automatic mock session and development step-up

**Files:**
- Modify: `web/nuxt.config.ts`
- Modify: `web/app/composables/useControlSession.ts`
- Modify: `web/app/composables/useSensitiveReveal.ts`
- Modify: `web/test/session.nuxt.spec.ts`
- Modify: `web/test/sensitive-reveal.nuxt.spec.ts`
- Modify: `web/test/generated-assets.unit.spec.ts`
- Modify: `web/e2e/shell.spec.ts`
- Modify: `web/scripts/scan-generated-assets.mjs`

**Interfaces:**
- Consumes: `useControlApi.request`, `import.meta.dev`, existing Mini App init data, existing Telegram browser step-up URL and production build scanning.
- Produces: `exchangeDevelopmentSession() -> Promise<boolean>`, `ensureStepUp(action, returnTo?) -> Promise<boolean>`, proxies for `/api/control`, `/auth`, `/telegram/webhook` and `/health`, and production-output forbidden markers for the dev UID key and dev-auth route.

- [x] **Step 1: Write failing session-bootstrap tests**

Make the first `/session` request reject with a literal 401 response shape, then prove a development build calls `/auth/dev` exactly once, accepts its real session payload and never calls Telegram OIDC. Prove non-401 failures do not call development login, Mini App init data still wins, and repeated bootstrap stays idempotent.

- [x] **Step 2: Run the session tests and verify RED**

Run from `web/`: `pnpm test -- session.nuxt.spec.ts`  
Expected: the 401 case remains anonymous and never calls `/auth/dev`.

- [x] **Step 3: Implement development-only automatic login**

Keep `refresh()` authoritative. In `bootstrap()`, after an exact anonymous result and only under `import.meta.dev`, POST an empty request to `/auth/dev`; compile the branch out otherwise. Preserve bounded error copy and do not automatically redirect to real OIDC.

```typescript
const restored = await refresh();
if (restored || status.value !== "anonymous") return restored;
if (import.meta.dev) return await exchangeDevelopmentSession();
return false;
```

- [x] **Step 4: Write failing exact-action frontend step-up tests**

In development, prove `ensureStepUp("credential:reveal")` posts `{ action: "credential:reveal" }` with current CSRF before the reveal request and aborts reveal when step-up fails. Prove signed Mini App init data takes precedence and uses `/auth/step-up/mini-app`. Add a built-production Playwright case for a regular browser that proves the helper navigates to `/auth/telegram/step-up/start?action=credential%3Areveal&return_to=%2Fproviders` and performs no reveal request before callback.

- [x] **Step 5: Run step-up/reveal tests and verify RED**

Run from `web/`: `pnpm test -- sensitive-reveal.nuxt.spec.ts session.nuxt.spec.ts`  
Expected: development reveal occurs without the required step-up request.

- [x] **Step 6: Implement `ensureStepUp` and wire sensitive reveal**

The dev branch POSTs `/auth/dev/step-up` and updates `session.step_up_at` from the returned `verified_at`. The Mini App branch submits current `window.Telegram.WebApp.initData`; the browser branch assigns the existing OIDC step-up URL. `useSensitiveReveal.reveal` returns before the secret request unless `ensureStepUp` succeeds.

- [x] **Step 7: Write failing proxy and production-artifact tests**

Exercise `nuxt.config.ts` through the existing workspace contract to prove `/api/control`, `/auth`, `/telegram/webhook` and `/health` target `PZP_API_DEV_ORIGIN ?? "http://127.0.0.1:8000"` with the mounted prefix repeated in each target, and prove the broad `/api` prefix is not reserved so Nuxt UI's icon API remains local. Extend the generated scanner test with fixture files containing `CONTROL_PLANE_DEV_USER_ID`, `/api/control/v1/auth/dev` and `/api/control/v1/auth/dev/step-up`, and prove each is rejected.

- [x] **Step 8: Implement complete development proxy map and scan boundary**

Add `/api/control`, `/auth`, `/telegram/webhook` and `/health`, all with `changeOrigin: true` and prefix-preserving targets. Add the development markers to the generated-output denylist and scan the complete `.output` tree rather than only public assets so SSR server chunks are covered.

- [x] **Step 9: Run focused frontend tests and verify GREEN**

Run from `web/`: `pnpm test -- session.nuxt.spec.ts sensitive-reveal.nuxt.spec.ts generated-assets.unit.spec.ts dev-coordinator.unit.spec.ts`  
Expected: all focused frontend tests pass.

### Task 6: Environment contract, operator documentation and living roadmap

**Files:**
- Modify: `.env.example`
- Modify: `README.md`
- Modify: `docs/setup.md`
- Modify: `docs/deployment.md`
- Modify: `docs/secrets.md`
- Modify: `docs/testing.md`
- Modify: `PLAN.md`
- Modify: `plans/021-provider-key-pools-and-telegram-control-plane.md`
- Modify: `plans/README.md` only if its Plan 021 status summary needs the new acceptance state
- Modify: `docs/THOUGHTS.md` only if the implementation reveals a durable non-obvious fact not already authoritative in the spec, plan, code or tests

**Interfaces:**
- Consumes: the verified commands, exact environment behavior and production boundary from Tasks 1-5.
- Produces: one canonical developer workflow and accurate `DONE`/`PARTIAL`/remaining roadmap state.

- [x] **Step 1: Add exact development environment examples**

Document `CONTROL_PLANE_DEV_USER_ID` as required only for `control-plane-dev`, and `CONTROL_PLANE_DEV_ORIGIN`/`CONTROL_PLANE_DEV_API_PORT` as optional. Keep all examples non-secret and state that backend settings come from the repository-root `.env` while Nuxt receives no credential or UID.

- [x] **Step 2: Document the three local modes and production mode**

Describe `pnpm dev:web` as frontend-only, `pnpm dev` with empty `WEBHOOK_URL` as combined offline mode, `pnpm dev` with complete HTTPS webhook configuration as connected mode, and `pnpm build && pnpm start`/Compose as production behavior. Include the warning that connected mode calls `setWebhook`, changes Telegram delivery mode, and needs a publicly reachable HTTPS webhook even though the browser origin remains loopback HTTP.

- [x] **Step 3: Document vault and authentication safety boundaries**

State that the ignored local key preserves development credential decryptability but is colocated convenience protection, corrupt key material is never replaced, production must use externally managed master keys, mock login uses only the backend-configured UID, exact-action development step-up still protects secrets, and dev routes are absent in production.

- [x] **Step 4: Update test/runbook commands and roadmap state**

Add raw `pnpm` and `uv run` commands, manual combined smoke steps, expected `/health` payload, automatic login expectations, shutdown validation and optional connected-mode checks. Update Plan 021 and `PLAN.md` only after the matching gates pass; distinguish code-complete offline acceptance from optional live Telegram and deferred production deployment acceptance.

- [x] **Step 5: Validate documentation and status consistency**

Run: `rtk git diff --check -- .env.example README.md docs/setup.md docs/deployment.md docs/secrets.md docs/testing.md PLAN.md plans/021-provider-key-pools-and-telegram-control-plane.md plans/README.md`  
Expected: no accidental patch corruption; trailing-space warnings are acceptable only for intentional repository-standard Markdown hard breaks.

### Task 7: Full static, test, production-boundary and end-to-end verification

**Files:**
- Modify only when a failing verification first receives a regression test and a minimal fix.

**Interfaces:**
- Consumes: every artifact from Tasks 1-6.
- Produces: fresh evidence for focused behavior, full suites, production output isolation and one bounded combined development smoke.

- [x] **Step 1: Run Python format and static checks**

Run: `uv run ruff format --check .`  
Run: `uv run ruff check .`  
Run: `uv run ty check`  
Expected: every command exits zero.

- [x] **Step 2: Run focused Python regression suites**

Run: `uv run pytest tests/test_control_plane_development.py tests/test_control_plane_api.py tests/test_control_plane_contract.py tests/test_webhook.py tests/test_cli.py tests/test_admin_alerts.py tests/test_provider_key_pool.py -q`  
Expected: all implemented tests pass; only pre-existing strict xfails for unimplemented unrelated Plan 021 contracts remain xfailed.

- [x] **Step 3: Run the full Python suite with a fresh external pytest base directory**

Run in a fresh PowerShell process:

```powershell
$verificationBase = Join-Path $env:TEMP ("pzp-control-plane-dev-" + [guid]::NewGuid())
uv run pytest -q --basetemp $verificationBase
```

Expected: zero failures; live credential-gated markers may skip when their credentials are not configured.

- [x] **Step 4: Run frontend format, lint, type, unit and OpenAPI checks**

Run from `web/`: `pnpm format:check`  
Run from `web/`: `pnpm lint`  
Run from `web/`: `pnpm typecheck`  
Run from `web/`: `pnpm test`  
Run from `web/`: `pnpm openapi:check`  
Expected: every command exits zero. Run `pnpm typecheck` directly so RTK cannot rewrite it to `tsc`.

- [x] **Step 5: Build and scan the production Nuxt output**

Run from `web/`: `pnpm build`  
Run from `web/`: `pnpm scan:generated`  
Run from `web/`: `pnpm smoke:ssr`  
Expected: production build succeeds, the complete generated output contains no development UID key, dev-auth route or secret value, and direct SSR routes retain CSP nonce/private-shell behavior.

- [x] **Step 6: Run Playwright desktop/mobile production checks**

Run from `web/`: `pnpm test:e2e`  
Expected: all configured browser projects pass without a Python development runtime.

- [x] **Step 7: Run a bounded combined offline development smoke**

Launch `pnpm dev` from `web/` with a disposable SQLite path, disposable memory directory, `CONTROL_PLANE_DEV_USER_ID=887758628`, empty `WEBHOOK_URL`, and dedicated free ports. Poll Nuxt and FastAPI for at most 60 seconds; assert `/health` is 200 with both critical tasks true; open `/providers` and verify automatic development login loads the bot-admin UI; create or query a provider through the same-origin proxy; send Ctrl+C; verify both Node/Nuxt and Python/Uvicorn children exit and both ports are released. Do not configure a Telegram token or webhook for this smoke.

- [x] **Step 8: Verify production routes and settings remain fail-closed**

Run `uv run pzp-bot webhook` with deliberately incomplete production control-plane settings in a disposable environment and assert safe nonzero startup before any external call. Inspect `create_control_plane_schema_app().openapi()` through `uv run python` and assert the two dev paths are absent. Run the built frontend server and assert a POST to the guessed dev route is not handled by Nuxt as an authentication API.

- [x] **Step 9: Reconcile the implementation against every acceptance criterion**

Read the approved design and this plan line by line, record the exact command evidence beside each acceptance criterion in Plan 021/`PLAN.md`, leave optional live Telegram connected-mode and Docker/production deployment explicitly unclaimed unless actually run, and append a durable thought only if a non-obvious implementation gotcha would otherwise be costly to rediscover.

- [x] **Step 10: Inspect final diff hygiene without mutating Git state**

Run: `rtk git status --short`  
Run: `rtk git diff --stat`  
Run: `rtk git diff --check`  
Expected: only intended implementation, tests, documentation and the pre-existing Phase 5 work are present; no generated caches, local development key, database, process logs or secrets are tracked.

## Final Verification Evidence

- TDD failures were observed before each implementation slice: missing development module, 404 development-auth/step-up routes, absent offline/connected runtime behavior, missing coordinator, session/reveal behavior without development exchange/step-up, Windows `spawn EINVAL`, stripped Nitro proxy paths, broad `/api` icon interception, unmanaged bootstrap-pool 404 UX and transient Windows wrapper ownership.
- Whole-project Python format/lint/type gates passed with 175 files already formatted, Ruff reporting all checks passed and `ty` reporting all checks passed. The focused Python development/control-plane/runtime slice passed with 99 tests and 4 deliberate strict xfails; the final fresh external-basetemp suite passed with 343 tests, 1 credential-gated live Telegram skip and the same 4 deliberate strict xfails.
- Frontend gates passed Oxfmt, type-aware Oxlint/tsgolint, raw `pnpm typecheck`, OpenAPI drift and 27 Vitest unit/component tests across 7 files. The final Nuxt 4.5.1 Node-server build was 2.8 MB, the complete generated-output scan passed across 227 text assets, direct public/overview/providers SSR smoke passed and all 16 Playwright checks passed across desktop Chromium and Pixel 7.
- The bounded real combined smoke used the fake provider, disposable SQLite/memory state, a non-secret test master key, blank webhook/token settings and dedicated loopback ports. Direct and proxied health returned 200 with both critical tasks healthy; direct `/providers` SSR returned 200; Chromium received the expected first-session 401, exchanged the backend-selected UID 887758628, rendered bot-admin provider operations, kept Nuxt's icon API local, omitted managed-pool requests for the environment bootstrap profile and navigated after hydration; exact-action `credential:reveal` development step-up returned 200.
- Windows process inspection proved the final backend and Nuxt listeners descend from persistent PowerShell 7 wrapper roots owned by the coordinator. Terminating the exact disposable backend wrapper tree caused the coordinator to exit nonzero, terminate the complete Nuxt sibling tree and release both ports inside the bounded check.
- The normal control-plane schema independently excluded `/api/control/v1/auth/dev` and `/api/control/v1/auth/dev/step-up`; production applications, OpenAPI and the complete generated Nuxt output contain no development-auth or UID surface. Test isolation also prevents the repository `.env` and real secrets from entering default pytest settings.
- Follow-up real-user startup caught and red-green regression-tested the dotenv boundary: the coordinator already launched the backend from the repository root, but the development builder read `os.environ` even though Pydantic dotenv loading does not export values there. Dedicated UID/origin/port values now travel through `Settings`, while explicit mapping injection remains available for isolated tests and normal polling/webhook runtimes do not act on those fields. The corrective checkpoint passed 52 focused development/CLI tests, full Ruff format/lint, `ty` and a bounded offline combined smoke whose proxied health and bot-admin session proved that UID 887758628 came from the repository `.env`.
- Optional real connected-Telegram development was verified with injected doubles and fail-closed configuration tests, not a live Telegram webhook mutation. Docker/Compose/Caddy/TLS, registered real Telegram OIDC/Mini App, authorized live provider failover/admin DM and backup/restore remain unclaimed Plan 021 Gate E work.
