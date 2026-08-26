import { expect, test, type Page, type Route } from "@playwright/test";

const baseUser = {
  telegram_user_id: 887758628,
  first_name: "Lu",
  last_name: "Lederrey",
  username: "lederrey",
  language_code: "en",
  photo_url: null,
  is_premium: true,
};

function session(
  options: {
    botAdmin?: boolean;
    chatCapabilities?: Record<string, string[]>;
    clientKind?: "browser" | "mini_app";
  } = {},
) {
  return {
    user: baseUser,
    client_kind: options.clientKind ?? "browser",
    authenticated_at: "2026-08-09T12:00:00Z",
    step_up_at: null,
    expires_at: "2026-08-16T12:00:00Z",
    csrf_token: "csrf-token",
    capabilities: options.botAdmin ? ["bot.admin"] : [],
    chat_capabilities: options.chatCapabilities ?? {},
  };
}

const overview = {
  scope: "all",
  generated_at: "2026-08-09T12:00:00Z",
  memory: {
    messages: 12,
    learned: 10,
    skipped: 2,
    knowledge_items: 4,
    embeddings: 4,
    pending_reviews: 1,
    audit_events: 8,
  },
  ai_usage: { total: 6, errors: 0, average_latency_ms: 220 },
  storage: {
    database_bytes: 4096,
    memory_bytes: 1024,
    memory_files: 2,
    memory_scan_truncated: false,
  },
  runtime: { uptime_seconds: 90, schema_revision: "0017", scheduler: [] },
  administration: {
    bot_admins: 2,
    delegated_chat_admins: 1,
    managed_chats: { active: 1, paused: 0, detached: 0 },
  },
  providers: {
    profiles: 1,
    credentials: { eligible: 1, total: 1 },
    local_dependency_available: true,
    local_models_loaded: 1,
  },
  alerts: { states: {} },
};

const catalog = {
  scope: "-1001",
  chat_models: ["gpt-5.6-terra"],
  embedding_models: ["text-embedding-3-small"],
  reasoning_levels: ["low", "medium", "high"],
  profiles: [],
};

async function fulfillJson(route: Route, body: unknown, status = 200): Promise<void> {
  await route.fulfill({ body: JSON.stringify(body), contentType: "application/json", status });
}

async function injectTelegram(page: Page, initData = "signed-phase10-init-data"): Promise<void> {
  await page.route("https://telegram.org/js/telegram-web-app.js*", (route) =>
    route.fulfill({ body: "", contentType: "application/javascript" }),
  );
  await page.addInitScript((signedInitData) => {
    const calls: string[] = [];
    Object.assign(window, { __telegramCalls: calls });
    Object.assign(window, {
      Telegram: {
        WebApp: {
          initData: signedInitData,
          colorScheme: "dark",
          themeParams: { bg_color: "#101010", text_color: "#f0f0f0" },
          ready: () => calls.push("ready"),
          expand: () => calls.push("expand"),
          setHeaderColor: () => calls.push("header"),
          setBackgroundColor: () => calls.push("background"),
          setBottomBarColor: () => calls.push("bottom"),
          BackButton: {
            show: () => calls.push("back:show"),
            hide: () => calls.push("back:hide"),
            onClick: () => calls.push("back:on"),
            offClick: () => calls.push("back:off"),
          },
          SettingsButton: {
            show: () => calls.push("settings:show"),
            hide: () => calls.push("settings:hide"),
            onClick: () => calls.push("settings:on"),
            offClick: () => calls.push("settings:off"),
          },
          enableClosingConfirmation: () => calls.push("closing:on"),
          disableClosingConfirmation: () => calls.push("closing:off"),
        },
      },
    });
  }, initData);
}

test("regular browsers expose only the bounded Telegram OIDC start URL", async ({ page }) => {
  await page.route("**/api/control/v1/session", (route) =>
    fulfillJson(route, { error: { code: "session_required", message: "Sign in." } }, 401),
  );

  await page.goto("/login?return_to=%2Fquery");

  await expect(page.getByRole("heading", { name: "Sign in with Telegram." })).toBeVisible();
  await expect(page.getByTestId("telegram-browser-login")).toHaveAttribute(
    "href",
    "/auth/telegram/start?return_to=%2Fquery",
  );
});

test("signed Mini App initData establishes a scoped session without browser fallback", async ({
  page,
}) => {
  const signedInitData = "query_id=phase10&auth_date=1786276800&hash=signed-fixture";
  await injectTelegram(page, signedInitData);
  let exchangeCount = 0;
  let sessionRefreshCount = 0;
  await page.route("**/api/control/v1/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace("/api/control/v1", "");
    if (path === "/auth/mini-app" && request.method() === "POST") {
      exchangeCount += 1;
      expect(request.postDataJSON()).toEqual({ init_data: signedInitData });
      return fulfillJson(
        route,
        session({ clientKind: "mini_app", chatCapabilities: { "-1001": ["chat.admin"] } }),
      );
    }
    if (path === "/session") {
      sessionRefreshCount += 1;
      return fulfillJson(route, {}, 401);
    }
    if (path === "/stats/overview") return fulfillJson(route, overview);
    if (path === "/stats/timeseries")
      return fulfillJson(route, { scope: "all", days: 14, points: [] });
    if (path === "/operations/health")
      return fulfillJson(route, {
        generated_at: "2026-08-09T12:00:00Z",
        checks: [],
        provider_calls_performed: false,
        runtime: overview.runtime,
        storage: overview.storage,
        providers: overview.providers,
        alerts: overview.alerts,
      });
    return fulfillJson(route, { error: { code: "unexpected", message: path } }, 500);
  });

  await page.goto("/");

  await expect(page.getByRole("heading", { name: "The bot at a glance." })).toBeVisible();
  await expect(page.getByText("Live control plane · -1001", { exact: true })).toBeVisible();
  await expect(page.getByText("Scoped administrator", { exact: true })).toHaveCount(1);
  await expect(page.getByRole("link", { name: "Providers" })).toHaveCount(0);
  await expect(page.locator("html")).toHaveAttribute("data-telegram-web-app", "true");
  expect(exchangeCount).toBe(1);
  expect(sessionRefreshCount).toBe(0);
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1),
  ).toBe(true);
});

test("scoped and ordinary identities cannot hydrate bot-wide or foreign-scope operations", async ({
  page,
}) => {
  let chatListCount = 0;
  await page.route("**/api/control/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname.replace("/api/control/v1", "");
    if (path === "/session")
      return fulfillJson(route, session({ chatCapabilities: { "-1001": ["chat.admin"] } }));
    if (path === "/chats") {
      chatListCount += 1;
      return fulfillJson(
        route,
        chatListCount === 1
          ? { items: [], next_cursor: null, has_more: false }
          : {
              items: [
                {
                  chat_id: -1001,
                  title: "Scoped group",
                  username: null,
                  status: "active",
                  version: 1,
                  updated_at: "2026-08-09T12:00:00Z",
                },
              ],
              next_cursor: null,
              has_more: false,
            },
      );
    }
    if (path === "/query/catalog")
      return fulfillJson(
        route,
        { error: { code: "chat_admin_required", message: "Denied." } },
        403,
      );
    return fulfillJson(route, { error: { code: "unexpected", message: path } }, 500);
  });

  await page.goto("/chats");
  await expect(page.getByText("No managed chats are visible")).toBeVisible();
  await expect(page.getByRole("link", { name: "Bot Settings" })).toHaveCount(0);
  await page.goto("/providers");
  await expect(page).toHaveURL(/\/unauthorized$/);
  await expect(
    page.getByRole("heading", { name: "This account cannot access that scope." }),
  ).toBeVisible();
  await page.goto("/query");
  await expect(page.getByRole("alert")).toContainText(
    "Your Telegram account does not have access to this scope.",
  );
});

test("browser feedback distinguishes 409, 422, 429, 403 and session-expiring 401", async ({
  page,
}) => {
  let catalogStatus = 200;
  await page.route("**/api/control/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname.replace("/api/control/v1", "");
    if (path === "/session") return fulfillJson(route, session({ botAdmin: true }));
    if (path === "/bot-settings/admins")
      return fulfillJson(route, [
        {
          telegram_user_id: 887758628,
          source: "bootstrap",
          version: null,
          granted_by: null,
          granted_at: null,
        },
      ]);
    if (path === "/bot-settings/runtime" && request.method() === "GET")
      return fulfillJson(route, [
        {
          key: "retrieval_top_k",
          value: 6,
          default_value: 6,
          previous_value: null,
          updated_by: null,
          updated_at: null,
          version: 1,
        },
      ]);
    if (path === "/bot-settings/runtime" && request.method() === "PATCH")
      return fulfillJson(route, { error: { code: "version_conflict", message: "Stale." } }, 409);
    if (path === "/chats")
      return fulfillJson(route, {
        items: [
          {
            chat_id: -1001,
            title: "Active group",
            username: null,
            status: "active",
            version: 1,
            updated_at: "2026-08-09T12:00:00Z",
          },
        ],
        next_cursor: null,
        has_more: false,
      });
    if (path === "/query/catalog")
      return catalogStatus === 200
        ? fulfillJson(route, catalog)
        : fulfillJson(route, { error: { code: "rate_limited", message: "Wait." } }, catalogStatus);
    if (path === "/providers" && request.method() === "GET") return fulfillJson(route, []);
    if (path === "/providers" && request.method() === "POST")
      return fulfillJson(route, { error: { code: "validation", message: "Invalid." } }, 422);
    if (path === "/alerts")
      return fulfillJson(route, { items: [], next_cursor: null, has_more: false });
    if (path === "/stats/overview")
      return fulfillJson(route, { error: { code: "scope_denied", message: "Denied." } }, 403);
    if (path === "/stats/timeseries")
      return fulfillJson(route, { scope: "all", days: 14, points: [] });
    if (path === "/operations/health")
      return fulfillJson(route, { error: { code: "session_invalid", message: "Expired." } }, 401);
    return fulfillJson(route, { error: { code: "unexpected", message: path } }, 500);
  });

  await page.goto("/bot-settings");
  await page.getByTestId("runtime-retrieval_top_k").fill("7");
  await page.getByTestId("save-runtime-retrieval_top_k").click();
  await expect(page.getByRole("alert")).toContainText("changed in another session");

  await page.goto("/providers");
  await page.getByTestId("new-profile-name").fill("invalid-profile");
  await page.getByTestId("new-profile-base-url").fill("https://provider.example.test/v1");
  await page.getByTestId("create-profile").click();
  await expect(page.getByRole("alert")).toContainText("highlighted values");

  catalogStatus = 429;
  await page.goto("/query");
  await expect(page.getByRole("alert")).toContainText("Too many requests");

  await page.goto("/stats");
  await expect(page.getByRole("alert")).toContainText("does not have access");

  await page.goto("/operations");
  await expect(page).toHaveURL(/\/login\?expired=1/);
  await expect(page.getByRole("alert")).toContainText("session expired");
});

test("provider profile and credential lifecycle stays secret-safe through reveal and manual mask", async ({
  page,
}) => {
  await injectTelegram(page);
  let profileCreated = false;
  let credential: Record<string, unknown> | null = null;
  let profileVersion = 1;
  const profile = () => ({
    name: "phase10",
    provider_kind: "openai_compatible",
    base_url: "https://api.openai.com/v1",
    chat_model: "gpt-5.6-terra",
    embedding_model: "text-embedding-3-small",
    chat_capable: true,
    embedding_capable: true,
    reasoning_capable: true,
    local: false,
    redact_before_ai: true,
    redaction_patterns: [],
    fake_embedding_dimensions: 64,
    enabled: true,
    source: "managed",
    version: profileVersion,
  });
  const pool = () => ({
    profile_name: "phase10",
    profile_revision: profileVersion,
    active_credential_fingerprint: credential ? "fingerprint" : null,
    active_credential_label: credential ? String(credential.label) : null,
    health_epoch: 0,
    total_credentials: credential ? 1 : 0,
    eligible_credentials: credential && credential.state === "enabled" ? 1 : 0,
    cooldown_credentials: 0,
    disabled_credentials: credential && credential.state === "disabled" ? 1 : 0,
    last_exhausted_at: null,
    last_recovered_at: null,
  });
  await page.route("**/api/control/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname.replace("/api/control/v1", "");
    if (path === "/auth/mini-app")
      return fulfillJson(route, session({ botAdmin: true, clientKind: "mini_app" }));
    if (path === "/auth/step-up/mini-app")
      return fulfillJson(route, {
        step_up: true,
        verified_at: "2026-08-09T12:01:00Z",
        action: request.postDataJSON().action,
      });
    if (path === "/providers" && request.method() === "GET")
      return fulfillJson(route, profileCreated ? [profile()] : []);
    if (path === "/providers" && request.method() === "POST") {
      expect(request.postDataJSON()).toMatchObject({
        name: "phase10",
        chat_model: "gpt-5.6-terra",
        embedding_model: "text-embedding-3-small",
      });
      profileCreated = true;
      return fulfillJson(route, profile());
    }
    if (path === "/alerts")
      return fulfillJson(route, { items: [], next_cursor: null, has_more: false });
    if (path === "/providers/phase10/credentials" && request.method() === "GET")
      return fulfillJson(route, credential ? [credential] : []);
    if (path === "/providers/phase10/credentials" && request.method() === "POST") {
      expect(request.postDataJSON()).toEqual({ label: "Primary test key", value: "sk-e2e-new" });
      credential = {
        id: 42,
        profile_name: "phase10",
        label: "Primary test key",
        source: "managed",
        environment_variable: null,
        last_four: "-new",
        fingerprint: "fingerprint",
        position: 0,
        state: "enabled",
        health_state: "unknown",
        cooldown_until: null,
        last_error_classification: null,
        success_count: 0,
        failure_count: 0,
        last_success_at: null,
        last_failure_at: null,
        version: 1,
        revealable: true,
        active: false,
      };
      return fulfillJson(route, credential);
    }
    if (path === "/providers/phase10/pool") return fulfillJson(route, pool());
    if (path === "/providers/phase10/credentials/42/replace") {
      expect(request.postDataJSON()).toMatchObject({
        version: credential?.version,
        label: "Rotated test key",
        value: "sk-e2e-rotated",
      });
      credential = {
        ...credential,
        label: "Rotated test key",
        last_four: "ated",
        version: Number(credential?.version) + 1,
      };
      return fulfillJson(route, credential);
    }
    if (path.endsWith("/test")) {
      const mode = request.postDataJSON().mode as "chat" | "embedding";
      credential = {
        ...credential,
        health_state: "healthy",
        success_count: Number(credential?.success_count) + 1,
      };
      return fulfillJson(route, {
        mode,
        provider_profile: "phase10",
        model: mode === "chat" ? "gpt-5.6-terra" : "text-embedding-3-small",
        attempted_credentials: 1,
        rotation_count: 0,
        final_classification: "success",
        health_state: "healthy",
      });
    }
    if (path === "/providers/phase10/credentials/42/reveal")
      return fulfillJson(route, {
        credential_id: 42,
        value: "sk-e2e-visible-only-after-click",
        expires_at: "2026-08-09T12:01:30Z",
      });
    if (path === "/providers/phase10/credentials/42" && request.method() === "PATCH") {
      credential = {
        ...credential,
        ...request.postDataJSON(),
        version: Number(credential?.version) + 1,
      };
      return fulfillJson(route, credential);
    }
    if (path === "/providers/phase10/credentials/42" && request.method() === "DELETE") {
      credential = null;
      return fulfillJson(route, { id: 42, audit_event_id: 900 });
    }
    return fulfillJson(route, { error: { code: "unexpected", message: path } }, 500);
  });

  await page.goto("/providers");
  await page.getByTestId("new-profile-name").fill("phase10");
  await page.getByTestId("new-profile-base-url").fill("https://api.openai.com/v1");
  await page.getByTestId("create-profile").click();
  await expect(page.getByText("phase10 is ready for an ordered credential pool.")).toBeVisible();

  await page.getByTestId("new-credential-label").fill("Primary test key");
  await page.getByTestId("new-credential-value").fill("sk-e2e-new");
  await page.getByTestId("create-credential").click();
  await expect(
    page.locator(".credential-card").getByText("Primary test key", { exact: true }),
  ).toBeVisible();
  await expect(page.getByText("sk-e2e-new")).toHaveCount(0);

  await page.getByTestId("replace-credential-label-42").fill("Rotated test key");
  await page.getByTestId("replace-credential-value-42").fill("sk-e2e-rotated");
  await page.getByTestId("replace-credential-42").click();
  await expect(
    page.locator(".credential-card").getByText("Rotated test key", { exact: true }),
  ).toBeVisible();
  await expect(page.getByText("sk-e2e-rotated")).toHaveCount(0);

  await page.getByTestId("test-chat-42").click();
  await expect(page.getByText("gpt-5.6-terra passed · success")).toBeVisible();
  await page.getByTestId("test-embedding-42").click();
  await expect(page.getByText("text-embedding-3-small passed · success")).toBeVisible();

  await page.getByTestId("reveal-secret").click();
  await expect(page.getByText("sk-e2e-visible-only-after-click")).toBeVisible();
  await page.getByTestId("mask-secret").click();
  await expect(page.getByText("sk-e2e-visible-only-after-click")).toHaveCount(0);

  await page.getByLabel("State").selectOption("disabled");
  await page.getByRole("button", { name: "Save order/state" }).click();
  await expect(page.locator('.credential-card .health-pill[data-state="healthy"]')).toBeVisible();

  await page.getByRole("button", { name: "Delete", exact: true }).click();
  await page.getByRole("button", { name: "Confirm credential deletion" }).click();
  await expect(
    page.getByText("No credentials are configured for this managed profile."),
  ).toBeVisible();
});

test("managed chat administration keeps pause, resume, detach, leave and forget distinct", async ({
  page,
}) => {
  await injectTelegram(page);
  let chat = {
    chat_id: -1001,
    title: "Phase 10 group",
    username: "phase10_group",
    status: "active",
    version: 1,
    updated_at: "2026-08-09T12:00:00Z",
  };
  let delegated: Array<Record<string, unknown>> = [];
  let scopeVersion = 0;
  const settingsPayload = () => ({
    scope: "-1001",
    effective: { retrieval_top_k: scopeVersion ? 9 : 6 },
    inherited: { retrieval_top_k: 6 },
    overrides: scopeVersion ? { retrieval_top_k: 9 } : {},
    policy: { learning_enabled: true, allow_admins: false },
    version: scopeVersion,
  });
  await page.route("**/api/control/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname.replace("/api/control/v1", "");
    if (path === "/auth/mini-app")
      return fulfillJson(route, session({ botAdmin: true, clientKind: "mini_app" }));
    if (path === "/auth/step-up/mini-app")
      return fulfillJson(route, {
        step_up: true,
        verified_at: "2026-08-09T12:02:00Z",
        action: request.postDataJSON().action,
      });
    if (path === "/chats" && request.method() === "GET")
      return fulfillJson(route, { items: [chat], next_cursor: null, has_more: false });
    if (path === "/chats/-1001/admins" && request.method() === "GET")
      return fulfillJson(route, delegated);
    if (path === "/chats/-1001/admins" && request.method() === "POST") {
      delegated = [
        {
          chat_id: -1001,
          telegram_user_id: request.postDataJSON().telegram_user_id,
          version: 1,
          granted_by: 887758628,
          granted_at: "2026-08-09T12:00:00Z",
        },
      ];
      return fulfillJson(route, delegated[0]);
    }
    if (path === "/chats/-1001/admins/854158484" && request.method() === "DELETE") {
      delegated = [];
      return fulfillJson(route, {
        chat_id: -1001,
        telegram_user_id: 854158484,
        version: 2,
        granted_by: 887758628,
        granted_at: "2026-08-09T12:00:00Z",
      });
    }
    if (path === "/chats/-1001/settings" && request.method() === "GET")
      return fulfillJson(route, settingsPayload());
    if (path === "/chats/-1001/settings" && request.method() === "PATCH") {
      expect(request.postDataJSON()).toMatchObject({ version: scopeVersion });
      scopeVersion += 1;
      return fulfillJson(route, { ...settingsPayload(), audit_event_id: 700 + scopeVersion });
    }
    if (path === "/chats/-1001/status" && request.method() === "PATCH") {
      expect(request.postDataJSON().version).toBe(chat.version);
      chat = {
        ...chat,
        status: request.postDataJSON().status,
        version: chat.version + 1,
      };
      return fulfillJson(route, { ...chat, audit_event_id: 800 + chat.version });
    }
    if (path === "/chats/-1001/leave" && request.method() === "POST") {
      expect(request.postDataJSON().confirmation).toBe("LEAVE -1001");
      chat = { ...chat, status: "detached", version: chat.version + 1 };
      return fulfillJson(route, chat);
    }
    if (path === "/chats/-1001/forget" && request.method() === "POST") {
      expect(request.postDataJSON()).toMatchObject({
        confirmation: "FORGET -1001",
        version: chat.version,
      });
      return fulfillJson(route, {
        chat_id: -1001,
        messages: 3,
        knowledge_items: 2,
        embeddings: 2,
        audit_event_id: 999,
      });
    }
    return fulfillJson(route, { error: { code: "unexpected", message: path } }, 500);
  });

  await page.goto("/chats");
  await expect(page.locator('.health-pill[data-state="active"]')).toBeVisible();

  await page.getByLabel("Telegram user ID").fill("854158484");
  await page.getByRole("button", { name: "Add for this chat" }).click();
  await expect(page.getByText("Telegram 854158484")).toBeVisible();
  await page.getByRole("button", { name: "Remove", exact: true }).click();
  await expect(page.getByText("Telegram 854158484")).toHaveCount(0);

  await page.getByLabel("retrieval_top_k").fill("9");
  await page.getByRole("button", { name: "Apply scope settings" }).click();
  await expect(page.getByText("Inherited: 6")).toBeVisible();

  await page.getByRole("button", { name: "Pause" }).click();
  await expect(page.locator('.health-pill[data-state="paused"]')).toBeVisible();
  await page.getByRole("button", { name: "Resume" }).click();
  await expect(page.locator('.health-pill[data-state="active"]')).toBeVisible();
  await page.getByRole("button", { name: "Detach" }).click();
  await expect(page.locator('.health-pill[data-state="detached"]')).toBeVisible();
  await page.getByRole("button", { name: "Resume" }).click();

  await page.getByLabel("Confirmation for -1001").fill("LEAVE -1001");
  await page.getByRole("button", { name: "Leave", exact: true }).click();
  await expect(page.getByText("was detached after the bot left.")).toBeVisible();

  await page.getByLabel("Confirmation for -1001").fill("FORGET -1001");
  await page.getByRole("button", { name: "Forget memory" }).click();
  await expect(
    page.getByText("memory was forgotten and projections were reconciled."),
  ).toBeVisible();
});
