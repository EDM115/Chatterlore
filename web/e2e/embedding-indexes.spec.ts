import { expect, test } from "./fixtures";

const session = {
  user: { telegram_user_id: 111, first_name: "Owner" },
  client_kind: "browser",
  authenticated_at: "2026-11-07T12:00:00Z",
  step_up_at: null,
  expires_at: "2026-11-14T12:00:00Z",
  csrf_token: "csrf-token",
  capabilities: ["bot.admin"],
  chat_capabilities: {},
};
const profile = {
  name: "new-embedding",
  embedding_model: "prepared/embed",
  embedding_capable: true,
  chat_capable: false,
  enabled: true,
  local: true,
  provider_kind: "local",
};
const oldSpace = {
  id: 2,
  key: "old-space",
  provider_profile: "old-embedding",
  model: "old/embed",
  dimension: 384,
  eligible: 10,
  indexed: 10,
  missing: 0,
  stale: 0,
  created_at: "2026-11-01T12:00:00Z",
};

test("index migration remains explicit through real production hydration and owner verification", async ({
  page,
}) => {
  const mutations: { path: string; body: unknown }[] = [];
  const browserErrors: string[] = [];
  page.on("pageerror", (error) => browserErrors.push(error.message));
  let job = {
    id: 7,
    scope: "all",
    provider_profile: profile.name,
    model: profile.embedding_model,
    space_id: 3,
    status: "queued",
    version: 1,
    processed: 0,
    total: 10,
    error_code: null,
    created_at: "2026-11-07T12:00:00Z",
    updated_at: "2026-11-07T12:00:00Z",
  };
  await page.route("**/api/control/v1/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace("/api/control/v1", "");
    if (request.method() !== "GET") mutations.push({ path, body: request.postDataJSON() });
    let body: unknown;
    if (path === "/session") body = session;
    else if (path === "/providers") body = [profile];
    else if (path === "/embedding-indexes")
      body = { scope: "all", active: oldSpace, spaces: [oldSpace], jobs: [] };
    else if (path === "/embedding-indexes/preview")
      body = {
        scope: "all",
        provider_profile: profile.name,
        model: profile.embedding_model,
        eligible: 10,
        indexed: 0,
        missing: 10,
        cost_warning: "This explicit job may incur provider usage charges.",
        local_preparation_required: true,
      };
    else if (path === "/embedding-indexes/jobs") body = job;
    else if (path === "/embedding-indexes/jobs/7/run") {
      job = { ...job, status: "ready", version: 2, processed: 10 };
      body = job;
    } else throw new Error(`Unexpected index request: ${path}`);
    await route.fulfill({ body: JSON.stringify(body), contentType: "application/json" });
  });
  await page.goto("/embedding-indexes");
  await expect(page.getByRole("heading", { name: "Embedding indexes", exact: true })).toBeVisible();
  await expect(page.getByText("old-embedding · old/embed").first()).toBeVisible();
  expect(mutations).toEqual([]);
  await page.getByTestId("preview-embedding-index").click();
  await expect(page.getByText("10 sources need indexing")).toBeVisible();
  await expect(page.getByRole("link", { name: "Open Providers" })).toHaveAttribute(
    "href",
    "/providers",
  );
  expect(mutations).toEqual([]);
  await page.getByTestId("start-embedding-job").click();
  await expect(page.getByText("Job 7 queued.", { exact: false })).toBeVisible();
  await page.getByTestId("run-embedding-job-7").click();
  await expect(page.getByText("10 / 10 sources processed")).toBeVisible();
  expect(mutations).toEqual([
    {
      path: "/embedding-indexes/jobs",
      body: { scope: "all", provider_profile: profile.name, model: profile.embedding_model },
    },
    { path: "/embedding-indexes/jobs/7/run", body: { max_batches: 1 } },
  ]);
  await page.getByTestId("activate-embedding-job-7").click();
  await expect(page.getByRole("alert")).toContainText("Type ACTIVATE 7 exactly");
  expect(mutations).toHaveLength(2);
  await page.route("**/auth/telegram/step-up/start?**", (route) =>
    route.fulfill({ body: "Fresh Telegram verification required", contentType: "text/html" }),
  );
  await page.getByTestId("embedding-confirmation-7").fill("ACTIVATE 7");
  await page.getByTestId("activate-embedding-job-7").click();
  await expect(page).toHaveURL(/action=embedding%3Aactivate/);
  expect(mutations).toHaveLength(2);
  expect(browserErrors).toEqual([]);
});

test("scoped admins cannot load owner-only index data", async ({ page }) => {
  const paths: string[] = [];
  await page.route("**/api/control/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    paths.push(path);
    if (path !== "/api/control/v1/session") throw new Error("Scoped page fetched owner data");
    await route.fulfill({
      body: JSON.stringify({
        ...session,
        capabilities: [],
        chat_capabilities: { "-1001": ["chat.admin"] },
      }),
      contentType: "application/json",
    });
  });
  await page.goto("/embedding-indexes");
  await expect(page).toHaveURL(/\/unauthorized$/);
  expect(paths).toEqual(["/api/control/v1/session"]);
});
