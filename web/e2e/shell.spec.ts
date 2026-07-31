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
