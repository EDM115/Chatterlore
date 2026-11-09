import { expect, test, type Page, type Route } from "./fixtures";

const session = {
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
  authenticated_at: "2026-08-02T12:00:00Z",
  step_up_at: null,
  expires_at: "2026-08-09T12:00:00Z",
  csrf_token: "csrf-token",
  capabilities: ["bot.admin"],
  chat_capabilities: {},
};

const runtime = { uptime_seconds: 3661, schema_revision: "0017", scheduler: [] };
const storage = {
  database_bytes: 1048576,
  memory_bytes: 2048,
  memory_files: 3,
  memory_scan_truncated: false,
};
const providers = {
  profiles: 2,
  credentials: { eligible: 2, total: 2 },
  local_dependency_available: true,
  local_models_loaded: 1,
};
const alerts = { states: { pending: 1, delivered: 8 } };
const overview = {
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
  storage,
  runtime,
  administration: {
    bot_admins: 2,
    delegated_chat_admins: 3,
    managed_chats: { active: 4, paused: 1, detached: 1 },
  },
  providers,
  alerts,
};
const catalog = {
  scope: "-1001",
  chat_models: ["gpt-6.1-terra"],
  embedding_models: ["text-embedding-3-small", "all-MiniLM-L6-v2"],
  reasoning_levels: ["low", "medium", "high"],
  profiles: [
    {
      name: "primary",
      provider_kind: "openai_compatible",
      source: "managed",
      enabled: true,
      chat_model: "gpt-6.1-terra",
      embedding_model: "text-embedding-3-small",
      capabilities: ["chat", "embedding", "reasoning"],
      availability: "ready",
      local_state: null,
      pool: { eligible: 2, total: 2 },
    },
    {
      name: "local",
      provider_kind: "local",
      source: "environment",
      enabled: true,
      chat_model: "none",
      embedding_model: "all-MiniLM-L6-v2",
      capabilities: ["embedding"],
      availability: "ready",
      local_state: { dependency_available: true, model_loaded: true },
      pool: null,
    },
  ],
};

async function fulfillJson(route: Route, body: unknown, status = 200): Promise<void> {
  await route.fulfill({ body: JSON.stringify(body), contentType: "application/json", status });
}

async function stubPhase8Api(page: Page): Promise<string[]> {
  const unexpected: string[] = [];
  await page.route("**/api/control/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = `${url.pathname.replace("/api/control/v1", "")}${url.search}`;

    if (path === "/session") return fulfillJson(route, session);
    if (path === "/chats?limit=100")
      return fulfillJson(route, {
        items: [
          {
            chat_id: -1001,
            title: "Active group",
            username: null,
            status: "active",
            version: 1,
            updated_at: "2026-08-02T10:00:00Z",
          },
        ],
        next_cursor: null,
        has_more: false,
      });
    if (path === "/stats/overview?scope=all") return fulfillJson(route, overview);
    if (path === "/stats/timeseries?scope=all&days=14")
      return fulfillJson(route, { scope: "all", days: 14, points: [] });
    if (path === "/stats/timeseries?scope=all&days=30")
      return fulfillJson(route, {
        scope: "all",
        days: 30,
        points: [
          {
            date: "2026-08-01",
            messages: 20,
            learned: 17,
            skipped: 3,
            knowledge_items: 6,
            ai_calls: 4,
            ai_errors: 1,
          },
        ],
      });
    if (path === "/operations/health")
      return fulfillJson(route, {
        generated_at: "2026-08-02T12:00:00Z",
        checks: ["db-integrity", "local-model-readiness", "projection-status", "provider-profile"],
        provider_calls_performed: false,
        runtime,
        storage,
        providers,
        alerts,
      });
    if (path === "/query/catalog?scope=-1001") return fulfillJson(route, catalog);
    if (path === "/query/search" && request.method() === "POST") {
      expect(request.postDataJSON()).toMatchObject({ scope: "-1001" });
      return fulfillJson(route, {
        destination_scope: "-1001",
        source_scopes: ["-1001"],
        memory_profile: null,
        selection: {
          retrieval_provider_profile: "primary",
          embedding_model: "text-embedding-3-small",
          chat_provider_profile: null,
          chat_model: null,
          reasoning_level: null,
        },
        results: [
          {
            content: "Keep Caddy as the one public origin.",
            score: 0.91,
            source: "summary",
            scope: "-1001",
            source_message_ids: [42, 43],
          },
        ],
        elapsed_ms: 18,
      });
    }
    if (path === "/query/ask" && request.method() === "POST") {
      expect(request.postDataJSON()).toMatchObject({
        question: "Why preserve one public origin?",
        scope: "-1001",
        retrieval_provider_profile: "local",
        embedding_model: "all-MiniLM-L6-v2",
        chat_provider_profile: "primary",
        chat_model: "gpt-6.1-terra",
        reasoning_level: "high",
      });
      return fulfillJson(route, {
        destination_scope: "-1001",
        source_scopes: ["-1001"],
        memory_profile: null,
        selection: {
          retrieval_provider_profile: "local",
          embedding_model: "all-MiniLM-L6-v2",
          chat_provider_profile: "primary",
          chat_model: "gpt-6.1-terra",
          reasoning_level: "high",
        },
        results: [
          {
            content: "Keep Caddy as the one public origin.",
            score: 0.91,
            source: "summary",
            scope: "-1001",
            source_message_ids: [42, 43],
          },
        ],
        elapsed_ms: 32,
        answer: "It keeps authentication cookies and backend routing on one trusted origin.",
        insufficient_context: false,
      });
    }
    if (path === "/memory/items?scope=all&limit=25&cursor=0")
      return fulfillJson(route, {
        items: [
          {
            id: 7,
            scope: "-1001",
            kind: "summary",
            content: "Deploy behind one Caddy origin.",
            source_message_ids: [41],
            metadata: { topic: "deployment" },
            created_at: "2026-08-01T10:00:00Z",
            updated_at: "2026-08-01T11:00:00Z",
          },
        ],
        next_cursor: null,
        has_more: false,
      });
    if (path === "/memory/reviews?scope=all&limit=25&cursor=0")
      return fulfillJson(route, {
        items: [
          {
            id: 12,
            scope: "-1001:22",
            kind: "summary",
            content: "Approve this compacted decision.",
            source_message_ids: [51],
            created_at: "2026-08-01T12:00:00Z",
          },
          {
            id: 13,
            scope: "-1001:22",
            kind: "summary",
            content: "Reject this stale compacted decision.",
            source_message_ids: [52],
            created_at: "2026-08-01T12:01:00Z",
          },
        ],
        next_cursor: null,
        has_more: false,
      });
    if (path === "/memory/reviews/12/approve" && request.method() === "POST")
      return fulfillJson(route, { review_id: 12, status: "approved", knowledge_item_id: 8 });
    if (path === "/memory/reviews/13/reject" && request.method() === "POST")
      return fulfillJson(route, { review_id: 13, status: "rejected", knowledge_item_id: null });
    if (path === "/memory/export?scope=all&format=md")
      return route.fulfill({
        body: "# Phase 10 memory export\n",
        contentType: "text/markdown; charset=utf-8",
        headers: { "content-disposition": 'attachment; filename="memory-all.md"' },
        status: 200,
      });
    if (path === "/memory/items/7/sources" && request.method() === "POST")
      return fulfillJson(route, {
        item_id: 7,
        scope: "-1001",
        sources: [
          {
            telegram_message_id: 41,
            user_id: 222,
            username: "alice",
            text: "Raw source revealed only after the explicit click.",
            created_at: "2026-08-01T09:58:00Z",
            message_permalink: "https://t.me/c/1001/41",
          },
        ],
      });
    if (path === "/audit/events?scope=all&limit=25")
      return fulfillJson(route, {
        items: [
          {
            id: 9,
            event_type: "credential_disabled",
            scope: null,
            user_id: 887758628,
            payload: { profile: "primary", fingerprint: "ab12" },
            created_at: "2026-08-02T11:00:00Z",
          },
        ],
        next_cursor: null,
        has_more: false,
      });
    if (path === "/audit/ai-usage?scope=all&limit=25")
      return fulfillJson(route, { items: [], next_cursor: null, has_more: false });
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
    if (path === "/bot-settings/runtime")
      return fulfillJson(route, [
        {
          key: "retrieval_top_k",
          value: 6,
          default_value: 6,
          previous_value: null,
          updated_by: null,
          updated_at: null,
          version: null,
        },
      ]);
    if (path === "/operations/checks/db-integrity" && request.method() === "POST")
      return fulfillJson(route, {
        check: "db-integrity",
        ok: true,
        result: "ok",
        details: {},
        warning: null,
        elapsed_ms: 2,
      });
    if (path === "/operations/checks/provider-profile" && request.method() === "POST") {
      expect(request.postDataJSON()).toEqual({
        profile_name: "primary",
        mode: "chat",
        allow_download: false,
      });
      return fulfillJson(route, {
        check: "provider-profile",
        ok: true,
        result: "ready",
        details: { profile_name: "primary", model: "gpt-6.1-terra" },
        warning: "This explicit manual check may incur provider usage charges.",
        elapsed_ms: 220,
      });
    }

    unexpected.push(`${request.method()} ${path}`);
    return fulfillJson(route, { detail: `Unexpected test request: ${path}` }, 500);
  });
  return unexpected;
}

test("new Phase 8 routes hydrate from direct navigation without exposing hidden reasoning", async ({
  page,
}) => {
  const unexpected = await stubPhase8Api(page);
  const routes = [
    ["/query", "Ask the memory, on your terms."],
    ["/memory", "Inspect what the bot remembers."],
    ["/stats", "See activity without scanning history."],
    ["/audit", "Changes leave a safe trail."],
    ["/operations", "Observe first. Trigger second."],
  ] as const;

  for (const [path, heading] of routes) {
    await page.goto(path);
    await expect(page.getByRole("heading", { name: heading })).toBeVisible();
  }

  await expect(page.getByText("No provider call performed")).toBeVisible();
  await expect(page.getByText("reasoning_content")).toHaveCount(0);
  expect(unexpected).toEqual([]);
});

test("Query and Memory keep sources explicit across a real browser interaction", async ({
  page,
}) => {
  const unexpected = await stubPhase8Api(page);
  await page.goto("/query");
  await page.getByTestId("query-input").fill("What did we decide?");
  await page.getByTestId("run-query").click();
  await expect(page.getByText("Keep Caddy as the one public origin.")).toBeVisible();
  await expect(page.getByText("Messages 42, 43")).toBeVisible();

  await page.getByTestId("query-mode").selectOption("ask");
  await page.getByTestId("retrieval-profile").selectOption("local");
  await page.getByLabel("Reasoning effort").selectOption("high");
  await page.getByTestId("query-input").fill("Why preserve one public origin?");
  await page.getByTestId("run-query").click();
  await expect(
    page.getByText("It keeps authentication cookies and backend routing on one trusted origin."),
  ).toBeVisible();

  await page.goto("/memory");
  await expect(page.getByText("Deploy behind one Caddy origin.")).toBeVisible();
  await expect(page.getByText("Raw source revealed only after the explicit click.")).toHaveCount(0);
  await expect(page.getByTestId("export-md")).toHaveAttribute(
    "href",
    "/api/control/v1/memory/export?scope=all&format=md",
  );
  const downloadPromise = page.waitForEvent("download");
  await page.getByTestId("export-md").click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toBe("memory-all.md");
  await page.getByTestId("raw-sources-7").click();
  await expect(page.getByText("Raw source revealed only after the explicit click.")).toBeVisible();
  await page.getByTestId("approve-review-12").click();
  await expect(page.getByText("Approve this compacted decision.")).toHaveCount(0);
  await page.getByTestId("reject-review-13").click();
  await expect(page.getByText("Reject this stale compacted decision.")).toHaveCount(0);
  expect(unexpected).toEqual([]);
});

test("explicit operational checks stay separate from cached health", async ({ page }) => {
  const unexpected = await stubPhase8Api(page);
  await page.goto("/operations");
  await expect(page.getByText("No provider call performed")).toBeVisible();

  await page.getByTestId("check-db-integrity").click();
  await expect(page.getByText("db-integrity · ok")).toBeVisible();
  await page.getByTestId("check-provider-profile").click();
  await expect(page.getByText("provider-profile · ready")).toBeVisible();
  await expect(page.getByText("gpt-6.1-terra")).toBeVisible();
  expect(unexpected).toEqual([]);
});

test("injected Telegram chrome follows routes and protects dirty settings", async ({ page }) => {
  await page.route("https://telegram.org/js/telegram-web-app.js*", (route) =>
    route.fulfill({ body: "", contentType: "application/javascript" }),
  );
  await page.addInitScript(() => {
    const calls: string[] = [];
    Object.assign(window, { __telegramCalls: calls });
    Object.assign(window, {
      Telegram: {
        WebApp: {
          initData: "",
          colorScheme: "dark",
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
  });
  const unexpected = await stubPhase8Api(page);
  await page.goto("/bot-settings");
  await expect(page.locator("html")).toHaveAttribute("data-telegram-web-app", "true");
  await page.getByLabel("Telegram user ID").fill("123456789");

  await expect
    .poll(() =>
      page.evaluate(() => (window as Window & { __telegramCalls: string[] }).__telegramCalls),
    )
    .toEqual(
      expect.arrayContaining([
        "ready",
        "expand",
        "header",
        "back:show",
        "settings:show",
        "closing:on",
      ]),
    );
  expect(unexpected).toEqual([]);
});
