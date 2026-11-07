import { expect, test, type Page } from "./fixtures";

const sessionPayload = {
  user: {
    telegram_user_id: 887758628,
    first_name: "Lu",
    last_name: "Lederrey",
    username: "lederrey",
    language_code: "en",
    photo_url: null,
    is_premium: true,
  },
  client_kind: "browser",
  authenticated_at: "2026-07-31T12:00:00Z",
  step_up_at: null,
  expires_at: "2026-08-07T12:00:00Z",
  csrf_token: "csrf-token",
  capabilities: ["bot.admin"],
  chat_capabilities: {},
};

async function stubAuthenticatedSession(page: Page): Promise<void> {
  await page.route("**/api/control/v1/session", (route) =>
    route.fulfill({ body: JSON.stringify(sessionPayload), contentType: "application/json" }),
  );
}

async function stubDashboard(page: Page): Promise<void> {
  await page.route("**/api/control/v1/stats/overview?scope=all", (route) =>
    route.fulfill({
      body: JSON.stringify({
        scope: "all",
        generated_at: "2026-08-02T12:00:00Z",
        memory: {
          messages: 180,
          learned: 150,
          skipped: 30,
          knowledge_items: 72,
          embeddings: 72,
          pending_reviews: 4,
          audit_events: 91,
        },
        ai_usage: { total: 44, errors: 2, average_latency_ms: 860 },
        storage: {
          database_bytes: 1048576,
          memory_bytes: 2048,
          memory_files: 3,
          memory_scan_truncated: false,
        },
        runtime: { uptime_seconds: 3661, schema_revision: "0017", scheduler: [] },
        administration: {
          bot_admins: 2,
          delegated_chat_admins: 3,
          managed_chats: { active: 4, paused: 1, detached: 1 },
        },
        providers: {
          profiles: 3,
          credentials: { eligible: 2, total: 3 },
          local_dependency_available: true,
          local_models_loaded: 1,
        },
        alerts: { states: { pending: 1, delivered: 8 } },
      }),
      contentType: "application/json",
    }),
  );
  await page.route("**/api/control/v1/stats/timeseries?scope=all&days=14", (route) =>
    route.fulfill({ body: JSON.stringify({ scope: "all", days: 14, points: [] }) }),
  );
  await page.route("**/api/control/v1/operations/health", (route) =>
    route.fulfill({
      body: JSON.stringify({
        generated_at: "2026-08-02T12:00:00Z",
        checks: ["db-integrity", "provider-profile"],
        provider_calls_performed: false,
        runtime: { uptime_seconds: 3661, schema_revision: "0017", scheduler: [] },
        storage: {
          database_bytes: 1048576,
          memory_bytes: 2048,
          memory_files: 3,
          memory_scan_truncated: false,
        },
        providers: {
          profiles: 3,
          credentials: { eligible: 2, total: 3 },
          local_dependency_available: true,
          local_models_loaded: 1,
        },
        alerts: { states: { pending: 1, delivered: 8 } },
      }),
      contentType: "application/json",
    }),
  );
}

async function stubProviderOperations(page: Page, credentials: object[] = []): Promise<void> {
  await page.route("**/api/control/v1/providers", (route) =>
    route.fulfill({
      body: JSON.stringify([
        {
          name: "primary",
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
          version: 1,
        },
      ]),
      contentType: "application/json",
    }),
  );
  await page.route("**/api/control/v1/providers/primary/credentials", (route) =>
    route.fulfill({ body: JSON.stringify(credentials), contentType: "application/json" }),
  );
  await page.route("**/api/control/v1/providers/primary/pool", (route) =>
    route.fulfill({
      body: JSON.stringify({
        profile_name: "primary",
        profile_revision: 1,
        active_credential_fingerprint: null,
        active_credential_label: null,
        health_epoch: 0,
        total_credentials: 0,
        eligible_credentials: 0,
        cooldown_credentials: 0,
        disabled_credentials: 0,
        last_exhausted_at: null,
        last_recovered_at: null,
      }),
      contentType: "application/json",
    }),
  );
  await page.route("**/api/control/v1/alerts?limit=25", (route) =>
    route.fulfill({
      body: JSON.stringify({ items: [], next_cursor: null, has_more: false }),
      contentType: "application/json",
    }),
  );
}

async function stubManagement(page: Page): Promise<void> {
  await page.route("**/api/control/v1/bot-settings/admins", (route) =>
    route.fulfill({
      body: JSON.stringify([
        {
          telegram_user_id: 887758628,
          source: "bootstrap",
          version: null,
          granted_by: null,
          granted_at: null,
        },
      ]),
      contentType: "application/json",
    }),
  );
  await page.route("**/api/control/v1/bot-settings/runtime", (route) =>
    route.fulfill({
      body: JSON.stringify([
        {
          key: "retrieval_top_k",
          value: 6,
          default_value: 6,
          previous_value: null,
          updated_by: null,
          updated_at: null,
          version: null,
        },
      ]),
      contentType: "application/json",
    }),
  );
  await page.route("**/api/control/v1/chats*", (route) =>
    route.fulfill({
      body: JSON.stringify({ items: [], next_cursor: null, has_more: false }),
      contentType: "application/json",
    }),
  );
}

test("keyboard users can skip navigation and focus the main content", async ({ page }) => {
  await stubAuthenticatedSession(page);
  await stubDashboard(page);
  const browserErrors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") browserErrors.push(message.text());
  });
  page.on("pageerror", (error) => browserErrors.push(error.message));

  await page.goto("/");
  await expect(page.getByRole("heading", { name: "The bot at a glance." })).toBeVisible();
  await expect(page.locator(".sidebar-note strong")).toHaveText("Bot administrator");

  expect(browserErrors).toEqual([]);

  await page.keyboard.press("Tab");
  await expect(page.locator(".skip-link")).toBeFocused();
  await page.keyboard.press("Enter");

  await expect(page.locator("#control-plane-content")).toBeFocused();
});

test("foundation routes retain distinct document titles", async ({ page }) => {
  await stubAuthenticatedSession(page);
  await stubDashboard(page);
  await page.goto("/");
  await expect(page).toHaveTitle("Dashboard · PZP AI Control Plane");
});

test("bot administrators can hydrate the direct provider operations route", async ({ page }) => {
  await stubAuthenticatedSession(page);
  await stubProviderOperations(page);
  await page.goto("/providers");

  await expect(
    page.getByRole("heading", { name: "Keys that fail without taking the bot down." }),
  ).toBeVisible();
  const mobileMenu = page.locator(".mobile-menu summary");
  if (await mobileMenu.isVisible()) {
    await mobileMenu.click();
    await expect(
      page.locator(".mobile-menu").getByRole("link", { name: "Providers" }),
    ).toHaveAttribute("aria-current", "page");
  } else {
    await expect(page.locator('.desktop-navigation a[aria-current="page"]')).toHaveText(
      "Providers",
    );
  }
  await expect(page.getByText("Provider operations may incur usage charges")).toBeVisible();
  await expect(
    page.getByText("No credentials are configured for this managed profile."),
  ).toBeVisible();
});

test("bot administrators can hydrate bot settings and managed-chat direct routes", async ({
  page,
}) => {
  await stubAuthenticatedSession(page);
  await stubManagement(page);

  await page.goto("/bot-settings");
  await expect(
    page.getByRole("heading", { name: "Owners and safe runtime controls." }),
  ).toBeVisible();
  await expect(page.getByText("Bootstrap owner", { exact: true })).toBeVisible();

  await page.goto("/chats");
  await expect(
    page.getByRole("heading", { name: "Chats stay inside their own boundary." }),
  ).toBeVisible();
  await expect(page.getByRole("heading", { name: "Add a Telegram group" })).toBeVisible();
  await expect(page.getByText("No managed chats are visible")).toBeVisible();
});

test("regular browsers use the bounded bot-DM enrollment fallback", async ({ page }) => {
  await stubAuthenticatedSession(page);
  await page.route("**/api/control/v1/chats*", (route) =>
    route.fulfill({
      body: JSON.stringify({ items: [], next_cursor: null, has_more: false }),
      contentType: "application/json",
    }),
  );
  await page.route("**/api/control/v1/chats/enrollment-requests", async (route) => {
    expect(route.request().postDataJSON()).toEqual({ delivery: "telegram_dm" });
    await route.fulfill({
      body: JSON.stringify({
        request_id: 88,
        status: "pending",
        delivery: "telegram_dm",
        expires_at: "2099-08-01T12:02:00Z",
        prepared_button_id: "prepared-fallback",
        chat_id: null,
        error_code: null,
      }),
      contentType: "application/json",
    });
  });
  await page.route("**/api/control/v1/chats/enrollment-requests/88", (route) =>
    route.fulfill({
      body: JSON.stringify({
        request_id: 88,
        status: "completed",
        delivery: "telegram_dm",
        expires_at: "2099-08-01T12:02:00Z",
        prepared_button_id: "prepared-fallback",
        chat_id: -1001,
        error_code: null,
      }),
      contentType: "application/json",
    }),
  );

  await page.goto("/chats");
  await page.getByTestId("enroll-chat").click();
  await expect(page.getByText("Telegram chat -1001 enrolled.")).toBeVisible();
});

test("production browser reveal starts exact-action Telegram step-up before secret access", async ({
  page,
}) => {
  await stubAuthenticatedSession(page);
  await stubProviderOperations(page, [
    {
      id: 42,
      profile_name: "primary",
      label: "Primary key",
      source: "managed",
      environment_variable: null,
      last_four: "1234",
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
    },
  ]);
  let stepUpUrl = "";
  let revealRequests = 0;
  await page.route("**/auth/telegram/step-up/start?*", (route) => {
    stepUpUrl = route.request().url();
    return route.fulfill({
      body: "<!doctype html><title>Telegram step-up</title>",
      contentType: "text/html",
    });
  });
  await page.route("**/api/control/v1/providers/primary/credentials/42/reveal", (route) => {
    revealRequests += 1;
    return route.fulfill({ status: 500 });
  });
  await page.goto("/providers");

  await page.getByTestId("reveal-secret").click();

  await expect.poll(() => stepUpUrl).not.toBe("");
  const requested = new URL(stepUpUrl);
  expect(requested.pathname).toBe("/auth/telegram/step-up/start");
  expect(requested.searchParams.get("action")).toBe("credential:reveal");
  expect(requested.searchParams.get("return_to")).toBe("/providers");
  expect(revealRequests).toBe(0);
});

test("anonymous browsers retain the sign-in document title", async ({ page }) => {
  await page.route("**/api/control/v1/session", (route) =>
    route.fulfill({ body: JSON.stringify({ detail: "authentication required" }), status: 401 }),
  );
  await page.goto("/login");
  await expect(page).toHaveTitle("Sign in · PZP AI Control Plane");
});

test("expired sessions return to login with an explicit recovery message", async ({ page }) => {
  await page.route("**/api/control/v1/session", (route) =>
    route.fulfill({ body: JSON.stringify({ detail: "authentication required" }), status: 401 }),
  );
  await page.goto("/login?expired=1");
  await expect(page.getByRole("alert")).toContainText("Your Telegram session expired");
  await expect(page.getByRole("link", { name: "Sign in with Telegram" })).toBeVisible();
});

test.describe("server-rendered direct routes", () => {
  test.use({ javaScriptEnabled: false });

  test("protected direct routes return a neutral access gate before hydration", async ({
    page,
  }) => {
    const response = await page.goto("/");

    expect(response?.status()).toBe(200);
    await expect(page.getByTestId("auth-pending")).toContainText("Checking Telegram access");
    await expect(page.getByRole("heading", { name: "The bot at a glance." })).toHaveCount(0);
    await expect(page.getByText("Opening the control plane…")).toHaveCount(0);
  });

  test("provider direct routes do not server-render privileged pool data", async ({ page }) => {
    const response = await page.goto("/providers");

    expect(response?.status()).toBe(200);
    await expect(page.getByTestId("auth-pending")).toContainText("Checking Telegram access");
    await expect(
      page.getByRole("heading", { name: "Keys that fail without taking the bot down." }),
    ).toHaveCount(0);
    await expect(page.getByText("Primary key")).toHaveCount(0);
  });

  test("new management routes keep privileged data out of server-rendered HTML", async ({
    page,
  }) => {
    for (const path of [
      "/bot-settings",
      "/chats",
      "/query",
      "/memory",
      "/stats",
      "/audit",
      "/operations",
    ]) {
      const response = await page.goto(path);
      expect(response?.status()).toBe(200);
      await expect(page.getByTestId("auth-pending")).toContainText("Checking Telegram access");
      await expect(page.getByText("Bootstrap owner")).toHaveCount(0);
      await expect(page.getByText("Shared Engineering")).toHaveCount(0);
    }
  });

  test("public login HTML carries the private SSR security policy", async ({ page }) => {
    const response = await page.goto("/login");

    expect(response?.status()).toBe(200);
    await expect(page.getByRole("heading", { name: "Sign in with Telegram." })).toBeVisible();
    expect(response?.headers()["cache-control"]).toBe("private, no-store");
    expect(response?.headers()["referrer-policy"]).toBe("no-referrer");
    expect(response?.headers()["content-security-policy"]).toContain(
      "frame-ancestors 'self' https://telegram.org https://*.telegram.org",
    );
  });
});
