import { expect, test, type Page } from "@playwright/test";

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

async function stubProviderOperations(page: Page): Promise<void> {
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
    route.fulfill({ body: "[]", contentType: "application/json" }),
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

test("keyboard users can skip navigation and focus the main content", async ({ page }) => {
  await stubAuthenticatedSession(page);
  const browserErrors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") browserErrors.push(message.text());
  });
  page.on("pageerror", (error) => browserErrors.push(error.message));

  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Private operations, one clear surface." }),
  ).toBeVisible();

  expect(browserErrors).toEqual([]);

  await page.keyboard.press("Tab");
  await expect(page.locator(".skip-link")).toBeFocused();
  await page.keyboard.press("Enter");

  await expect(page.locator("#control-plane-content")).toBeFocused();
});

test("foundation routes retain distinct document titles", async ({ page }) => {
  await stubAuthenticatedSession(page);
  await page.goto("/");
  await expect(page).toHaveTitle("Overview · PZP AI Control Plane");
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

test("anonymous browsers retain the sign-in document title", async ({ page }) => {
  await page.route("**/api/control/v1/session", (route) =>
    route.fulfill({ body: JSON.stringify({ detail: "authentication required" }), status: 401 }),
  );
  await page.goto("/login");
  await expect(page).toHaveTitle("Sign in · PZP AI Control Plane");
});

test.describe("server-rendered direct routes", () => {
  test.use({ javaScriptEnabled: false });

  test("protected direct routes return a neutral access gate before hydration", async ({
    page,
  }) => {
    const response = await page.goto("/");

    expect(response?.status()).toBe(200);
    await expect(page.getByTestId("auth-pending")).toContainText("Checking Telegram access");
    await expect(
      page.getByRole("heading", { name: "Private operations, one clear surface." }),
    ).toHaveCount(0);
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
