import { clearNuxtState, useState } from "#app";
import { mountSuspended } from "@nuxt/test-utils/runtime";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ProvidersPage from "~/pages/providers.vue";
import BotSettingsPage from "~/pages/bot-settings.vue";
import QueryPage from "~/pages/query.vue";
import ChatsPage from "~/pages/chats.vue";

const localProfile = {
  name: "local-text",
  provider_kind: "local",
  base_url: "",
  chat_model: "prepared/instruct",
  embedding_model: "",
  chat_capable: true,
  embedding_capable: false,
  reasoning_capable: false,
  local: true,
  redact_before_ai: true,
  redaction_patterns: [],
  fake_embedding_dimensions: 64,
  enabled: true,
  source: "managed",
  version: 1,
  api_dialect: "generic",
  auth_mode: "none",
  local_chat_options: { device: "cpu", local_files_only: true, max_new_tokens: 128 },
  local_embedding_options: {},
};
const catalog = {
  scope: "-1001",
  chat_models: ["prepared/instruct"],
  embedding_models: ["prepared/embed"],
  reasoning_levels: ["medium"],
  profiles: [
    {
      ...localProfile,
      embedding_model: "prepared/embed",
      capabilities: ["chat", "embedding"],
      availability: "ready",
      local_state: { dependency_available: true, model_loaded: false },
      pool: null,
    },
  ],
};

beforeEach(() => {
  clearNuxtState();
  useState("control-plane-session").value = {
    user: { telegram_user_id: 111, first_name: "Owner" },
    client_kind: "browser",
    authenticated_at: "2026-11-07T12:00:00Z",
    step_up_at: null,
    expires_at: "2026-11-14T12:00:00Z",
    csrf_token: "csrf-token",
    capabilities: ["bot.admin"],
    chat_capabilities: {},
  };
  useState("control-plane-session-status").value = "authenticated";
  useState("control-plane-session-bootstrapped").value = true;
  useState("control-plane-csrf").value = "csrf-token";
});
afterEach(() => vi.unstubAllGlobals());

describe("mixed provider controls", () => {
  it("runs explicit profile-level chat and embedding tests without a dummy credential", async () => {
    const request = vi.fn(
      async (input: string, options?: { body?: { mode?: string }; method?: string }) => {
        if (input.endsWith("/providers"))
          return [{ ...localProfile, embedding_capable: true, embedding_model: "prepared/embed" }];
        if (input.endsWith("/providers/local-text/test"))
          return { success: true, mode: options?.body?.mode, detail: "Provider check completed." };
        if (input.includes("/alerts?")) return { items: [], next_cursor: null, has_more: false };
        throw new Error(`Unexpected request: ${input}`);
      },
    );
    vi.stubGlobal("$fetch", request);
    const wrapper = await mountSuspended(ProvidersPage);
    await vi.waitFor(() =>
      expect(wrapper.find('[data-testid="test-profile-chat"]').exists()).toBe(true),
    );
    expect(request.mock.calls.some(([input]) => input.endsWith("/test"))).toBe(false);
    await wrapper.get('[data-testid="test-profile-chat"]').trigger("click");
    await vi.waitFor(() => expect(wrapper.text()).toContain("Chat check passed"));
    await wrapper.get('[data-testid="test-profile-embedding"]').trigger("click");
    await vi.waitFor(() => expect(wrapper.text()).toContain("Embedding check passed"));
    expect(request).toHaveBeenCalledWith(
      "/api/control/v1/providers/local-text/test",
      expect.objectContaining({ body: { mode: "chat" }, method: "POST" }),
    );
    expect(request).toHaveBeenCalledWith(
      "/api/control/v1/providers/local-text/test",
      expect.objectContaining({ body: { mode: "embedding" }, method: "POST" }),
    );
    expect(request.mock.calls.some(([input]) => input.includes("/credentials"))).toBe(false);
  });

  it("prepares cached models without downloads and requires verification for an explicit download", async () => {
    const request = vi.fn(
      async (
        input: string,
        options?: { body?: { mode?: string; action?: string }; method?: string },
      ) => {
        if (input.endsWith("/providers")) return [localProfile];
        if (input.endsWith("/providers/local-text/prepare-local"))
          return {
            provider_profile: "local-text",
            mode: "chat",
            loaded: true,
            downloaded: false,
            download_bytes: 0,
          };
        if (input.endsWith("/auth/dev/step-up"))
          return {
            step_up: true,
            action: options?.body?.action,
            verified_at: "2026-11-07T12:01:00Z",
          };
        if (input.includes("/alerts?")) return { items: [], next_cursor: null, has_more: false };
        throw new Error(`Unexpected request: ${input}`);
      },
    );
    vi.stubGlobal("$fetch", request);
    const wrapper = await mountSuspended(ProvidersPage);
    await vi.waitFor(() =>
      expect(wrapper.find('[data-testid="prepare-profile-chat"]').exists()).toBe(true),
    );
    await wrapper.get('[data-testid="prepare-profile-chat"]').trigger("click");
    await vi.waitFor(() => expect(wrapper.text()).toContain("prepared from local files"));
    expect(request).toHaveBeenCalledWith(
      "/api/control/v1/providers/local-text/prepare-local",
      expect.objectContaining({ body: { mode: "chat", allow_download: false }, method: "POST" }),
    );
    expect(request.mock.calls.some(([input]) => input.includes("step-up"))).toBe(false);
    await wrapper.get('[data-testid="allow-profile-download"]').setValue(true);
    await wrapper.get('[data-testid="prepare-profile-chat"]').trigger("click");
    await vi.waitFor(() =>
      expect(request).toHaveBeenCalledWith(
        "/api/control/v1/providers/local-text/prepare-local",
        expect.objectContaining({ body: { mode: "chat", allow_download: true } }),
      ),
    );
    expect(request).toHaveBeenCalledWith(
      "/api/control/v1/auth/dev/step-up",
      expect.objectContaining({ body: { action: "local_model:download" } }),
    );
  });
  it("keeps an unavailable runtime route visibly selected instead of presenting it as fallback", async () => {
    const request = vi.fn(async (input: string) => {
      if (input.endsWith("/bot-settings/admins")) return [];
      if (input.endsWith("/bot-settings/runtime"))
        return [
          {
            key: "summary_provider_profile",
            value: "disabled-provider",
            default_value: "",
            previous_value: null,
            updated_by: null,
            updated_at: null,
            version: 1,
          },
        ];
      if (input.endsWith("/chats?limit=100"))
        return { items: [{ chat_id: -1001, status: "active" }] };
      if (input.includes("/query/catalog?")) return catalog;
      throw new Error(`Unexpected request: ${input}`);
    });
    vi.stubGlobal("$fetch", request);
    const wrapper = await mountSuspended(BotSettingsPage);
    await vi.waitFor(() =>
      expect(wrapper.find('select[data-testid="runtime-summary_provider_profile"]').exists()).toBe(
        true,
      ),
    );
    const select = wrapper.get('[data-testid="runtime-summary_provider_profile"]');
    expect(select.element).toHaveProperty("value", "disabled-provider");
    expect(select.text()).toContain("disabled-provider · unavailable or incompatible");
  });
  it("shows provider-model edit conflicts as an index migration without leaking the backend message", async () => {
    const request = vi.fn(async (input: string, options?: { method?: string }) => {
      if (input.endsWith("/providers")) return [localProfile];
      if (input.endsWith("/providers/local-text") && options?.method === "PATCH")
        throw {
          response: { status: 409 },
          data: {
            error: { code: "embedding_migration_required", message: "private profile data" },
          },
        };
      if (input.includes("/alerts?")) return { items: [], next_cursor: null, has_more: false };
      throw new Error(`Unexpected request: ${input}`);
    });
    vi.stubGlobal("$fetch", request);
    const wrapper = await mountSuspended(ProvidersPage);
    await vi.waitFor(() =>
      expect(wrapper.find('[data-testid="edit-profile-kind"]').exists()).toBe(true),
    );
    await wrapper
      .findAll("button")
      .find((button) => button.text() === "Save profile")!
      .trigger("click");
    await vi.waitFor(() => expect(wrapper.get('[role="alert"]').text()).toContain("migration"));
    expect(wrapper.find('a[href="/embedding-indexes"]').exists()).toBe(true);
    expect(wrapper.text()).not.toContain("private profile data");
  });

  it("lets scoped admins reset an independent provider route without fetching owner-only profiles", async () => {
    const state = useState<Record<string, unknown>>("control-plane-session");
    state.value = {
      ...state.value,
      capabilities: [],
      chat_capabilities: { "-1001": ["chat.admin"] },
    };
    const settings = {
      scope: "-1001",
      effective: {
        chat_provider_profile: "local-text",
        chat_model: "prepared/instruct",
        summary_provider_profile: "local-text",
        embedding_provider_profile: "local-text",
      },
      inherited: { chat_provider_profile: "primary", chat_model: "other-vendor" },
      overrides: { chat_provider_profile: "local-text" },
      policy: { learning_enabled: true, allow_admins: false },
      version: 3,
    };
    const request = vi.fn(async (input: string, options?: { method?: string }) => {
      if (input.endsWith("/chats?limit=100"))
        return {
          items: [{ chat_id: -1001, title: "Scoped group", status: "active", version: 1 }],
          has_more: false,
          next_cursor: null,
        };
      if (input.endsWith("/chats/-1001/settings"))
        return options?.method === "PATCH" ? { ...settings, overrides: {}, version: 4 } : settings;
      if (input.includes("/query/catalog?scope=-1001")) return catalog;
      throw new Error(`Unexpected request: ${input}`);
    });
    vi.stubGlobal("$fetch", request);
    const wrapper = await mountSuspended(ChatsPage);
    await vi.waitFor(() =>
      expect(wrapper.find('[data-testid="scope-chat_provider_profile"]').exists()).toBe(true),
    );
    await wrapper.get('[data-testid="scope-chat_provider_profile"]').setValue("");
    await wrapper
      .findAll("button")
      .find((button) => button.text() === "Apply scope settings")!
      .trigger("click");
    await vi.waitFor(() => expect(wrapper.text()).toContain("were applied"));
    expect(request).toHaveBeenCalledWith(
      "/api/control/v1/chats/-1001/settings",
      expect.objectContaining({
        body: {
          settings: { chat_provider_profile: null },
          learning_enabled: true,
          allow_admins: false,
          version: 3,
        },
      }),
    );
    expect(request.mock.calls.some(([input]) => input.endsWith("/providers"))).toBe(false);
    expect(wrapper.find('a[href="/embedding-indexes"]').exists()).toBe(false);
  });
  it("creates a direct local chat-only profile without a dummy embedding model or credential", async () => {
    const request = vi.fn(async (input: string, options?: { method?: string; body?: unknown }) => {
      if (input.endsWith("/providers") && options?.method === "POST") return localProfile;
      if (input.endsWith("/providers")) return [];
      if (input.endsWith("/providers/local-text/credentials")) return [];
      if (input.endsWith("/providers/local-text/pool")) return null;
      if (input.includes("/alerts?")) return { items: [], next_cursor: null, has_more: false };
      throw new Error(`Unexpected request: ${input}`);
    });
    vi.stubGlobal("$fetch", request);
    const wrapper = await mountSuspended(ProvidersPage);
    await wrapper.get('[data-testid="new-profile-name"]').setValue("local-text");
    await wrapper.get('[data-testid="new-profile-kind"]').setValue("local");
    await wrapper.get('[data-testid="new-profile-chat-model"]').setValue("prepared/instruct");
    await wrapper.get('[data-testid="new-profile-embedding-capable"]').setValue(false);
    await wrapper.get('[data-testid="new-profile-chat-device"]').setValue("cpu");
    await wrapper.get('[data-testid="new-profile-chat-max-new-tokens"]').setValue(128);
    await wrapper.get('[data-testid="create-profile"]').trigger("click");
    await vi.waitFor(() =>
      expect(wrapper.find('[data-testid="edit-profile-auth-mode"]').exists()).toBe(true),
    );
    expect(request).toHaveBeenCalledWith(
      "/api/control/v1/providers",
      expect.objectContaining({
        method: "POST",
        body: expect.objectContaining({
          provider_kind: "local",
          chat_model: "prepared/instruct",
          embedding_model: "",
          embedding_capable: false,
          local: true,
          auth_mode: "none",
          local_chat_options: expect.objectContaining({
            device: "cpu",
            max_new_tokens: 128,
            local_files_only: true,
          }),
          local_embedding_options: expect.objectContaining({ normalize_embeddings: true }),
        }),
      }),
    );
    expect(wrapper.find('[data-testid="new-credential-value"]').exists()).toBe(false);
  });

  it("sends explicit local-server no-auth settings without requesting a credential pool", async () => {
    const server = {
      ...localProfile,
      provider_kind: "openai_compatible",
      base_url: "http://localhost:11434/v1",
    };
    const request = vi.fn(async (input: string, options?: { method?: string }) => {
      if (input.endsWith("/providers")) return [server];
      if (input.endsWith("/providers/local-text") && options?.method === "PATCH")
        return { ...server, version: 2 };
      if (input.includes("/alerts?")) return { items: [], next_cursor: null, has_more: false };
      throw new Error(`Unexpected request: ${input}`);
    });
    vi.stubGlobal("$fetch", request);
    const wrapper = await mountSuspended(ProvidersPage);
    await vi.waitFor(() =>
      expect(wrapper.find('[data-testid="edit-profile-auth-mode"]').exists()).toBe(true),
    );
    expect(wrapper.get('[data-testid="edit-profile-auth-mode"]').element).toHaveProperty(
      "value",
      "none",
    );
    await wrapper
      .findAll("button")
      .find((button) => button.text() === "Save profile")!
      .trigger("click");
    await vi.waitFor(() => expect(wrapper.text()).toContain("updated without a bot restart"));
    expect(request).toHaveBeenCalledWith(
      "/api/control/v1/providers/local-text",
      expect.objectContaining({
        body: expect.objectContaining({
          api_dialect: "generic",
          auth_mode: "none",
          local: true,
          version: 1,
        }),
      }),
    );
    expect(
      request.mock.calls.some(
        ([input]) => input.includes("/credentials") || input.endsWith("/pool"),
      ),
    ).toBe(false);
    expect(wrapper.find('input[type="password"]').exists()).toBe(false);
  });

  it("labels independent runtime routes and explains migration-required without copying a server error", async () => {
    const request = vi.fn(async (input: string, options?: { method?: string }) => {
      if (input.endsWith("/bot-settings/admins")) return [];
      if (input.endsWith("/bot-settings/runtime") && options?.method === "PATCH")
        throw {
          response: { status: 409 },
          data: { error: { code: "embedding_migration_required", message: "private model path" } },
        };
      if (input.endsWith("/bot-settings/runtime"))
        return ["chat", "summary", "embedding"].map((operation) => ({
          key: `${operation}_provider_profile`,
          value: "",
          default_value: "",
          previous_value: null,
          updated_by: null,
          updated_at: null,
          version: 2,
        }));
      if (input.endsWith("/chats?limit=100"))
        return { items: [{ chat_id: -1001, status: "active" }] };
      if (input.includes("/query/catalog?")) return catalog;
      throw new Error(`Unexpected request: ${input}`);
    });
    vi.stubGlobal("$fetch", request);
    const wrapper = await mountSuspended(BotSettingsPage);
    await vi.waitFor(() => expect(wrapper.text()).toContain("Answer provider"));
    expect(wrapper.text()).toContain("Summary provider");
    expect(wrapper.text()).toContain("Embedding provider");
    await wrapper.get('[data-testid="runtime-embedding_provider_profile"]').setValue("local-text");
    await wrapper.get('[data-testid="save-runtime-embedding_provider_profile"]').trigger("click");
    await vi.waitFor(() => expect(wrapper.get('[role="alert"]').text()).toContain("migration"));
    expect(wrapper.find('a[href="/embedding-indexes"]').exists()).toBe(true);
    expect(wrapper.text()).not.toContain("private model path");
  });

  it("allows local Ask without sending an unsupported reasoning effort", async () => {
    const request = vi.fn(async (input: string, options?: { body?: unknown }) => {
      if (input.endsWith("/chats?limit=100"))
        return { items: [{ chat_id: -1001, status: "active" }] };
      if (input.includes("/query/catalog?")) return catalog;
      if (input.endsWith("/query/ask"))
        return {
          answer: "Local answer",
          insufficient_context: false,
          destination_scope: "-1001",
          source_scopes: [],
          selection: options?.body,
          results: [],
          elapsed_ms: 1,
        };
      throw new Error(`Unexpected request: ${input}`);
    });
    vi.stubGlobal("$fetch", request);
    const wrapper = await mountSuspended(QueryPage);
    await vi.waitFor(() => expect(wrapper.find('[data-testid="query-mode"]').exists()).toBe(true));
    await wrapper.get('[data-testid="query-mode"]').setValue("ask");
    await wrapper.get('[data-testid="query-input"]').setValue("What is local?");
    await wrapper.get('[data-testid="run-query"]').trigger("click");
    await vi.waitFor(() => expect(wrapper.text()).toContain("Local answer"));
    expect(request).toHaveBeenCalledWith(
      "/api/control/v1/query/ask",
      expect.objectContaining({
        body: expect.objectContaining({
          chat_provider_profile: "local-text",
          reasoning_level: null,
        }),
      }),
    );
  });
});
