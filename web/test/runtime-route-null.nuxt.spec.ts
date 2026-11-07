import { clearNuxtState, useState } from "#app";
import { mountSuspended } from "@nuxt/test-utils/runtime";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import BotSettingsPage from "~/pages/bot-settings.vue";

const routeKeys = [
  "chat_provider_profile",
  "summary_provider_profile",
  "embedding_provider_profile",
];
const catalog = {
  scope: "-1001",
  chat_models: ["local/chat"],
  embedding_models: ["local/embed"],
  reasoning_levels: [],
  profiles: [
    {
      name: "local-profile",
      provider_kind: "local",
      source: "managed",
      enabled: true,
      chat_model: "local/chat",
      embedding_model: "local/embed",
      capabilities: ["chat", "embedding"],
      availability: "ready",
      local_state: null,
      pool: null,
    },
  ],
};

beforeEach(() => {
  clearNuxtState();
  useState("control-plane-session").value = {
    user: {
      telegram_user_id: 111,
      first_name: "Owner",
      last_name: null,
      username: null,
      language_code: "en",
      photo_url: null,
      is_premium: null,
    },
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

function runtimeSetting(key: string, value: string | number | boolean | null, version = 2) {
  return {
    key,
    value,
    default_value: null,
    previous_value: null,
    updated_by: null,
    updated_at: null,
    version,
  };
}

function stubSettings(initialValue: string | null, mutationValue: string | null = initialValue) {
  const request = vi.fn(async (input: string, options?: { method?: string }) => {
    if (input.endsWith("/bot-settings/admins")) return [];
    if (input.endsWith("/bot-settings/runtime") && options?.method === "PATCH")
      return {
        items: [
          ...routeKeys.map((key) => runtimeSetting(key, mutationValue, 3)),
          runtimeSetting("learning_default_enabled", true, 3),
          runtimeSetting("raw_message_retention_days", null, 3),
        ],
        version: 3,
        audit_event_id: 42,
      };
    if (input.endsWith("/bot-settings/runtime"))
      return [
        ...routeKeys.map((key) => runtimeSetting(key, initialValue)),
        runtimeSetting("learning_default_enabled", true),
        runtimeSetting("raw_message_retention_days", null),
      ];
    if (input.endsWith("/chats?limit=100"))
      return {
        items: [
          {
            chat_id: -1001,
            title: "Managed chat",
            username: null,
            status: "active",
            version: 1,
            updated_at: "2026-11-07T12:00:00Z",
          },
        ],
        next_cursor: null,
        has_more: false,
      };
    if (input.endsWith("/query/catalog?scope=-1001")) return catalog;
    throw new Error(`Unexpected request: ${input}`);
  });
  vi.stubGlobal("$fetch", request);
  return request;
}

describe("optional runtime provider routes", () => {
  it.each(routeKeys)("shows null %s as fallback and saves it as null", async (key) => {
    const request = stubSettings(null);
    const wrapper = await mountSuspended(BotSettingsPage);
    await vi.waitFor(() =>
      expect(wrapper.find(`select[data-testid="runtime-${key}"]`).exists()).toBe(true),
    );
    const select = wrapper.get(`[data-testid="runtime-${key}"]`);
    expect(select.element).toHaveProperty("value", "");
    expect(select.findAll("option").some((option) => option.attributes("value") === "off")).toBe(
      false,
    );
    await wrapper.get(`[data-testid="save-runtime-${key}"]`).trigger("click");
    await vi.waitFor(() => expect(wrapper.text()).toContain(`${key} was updated and reloaded.`));
    expect(request).toHaveBeenCalledWith(
      "/api/control/v1/bot-settings/runtime",
      expect.objectContaining({
        method: "PATCH",
        body: { updates: { [key]: null }, version: 2 },
      }),
    );
    expect(select.element).toHaveProperty("value", "");
  });

  it.each(routeKeys)(
    "clears named %s to null and keeps the mutation response at fallback",
    async (key) => {
      const request = stubSettings("local-profile", null);
      const wrapper = await mountSuspended(BotSettingsPage);
      await vi.waitFor(() =>
        expect(wrapper.find(`select[data-testid="runtime-${key}"]`).exists()).toBe(true),
      );
      await wrapper.get(`[data-testid="runtime-${key}"]`).setValue("");
      await wrapper.get(`[data-testid="save-runtime-${key}"]`).trigger("click");
      await vi.waitFor(() => expect(wrapper.text()).toContain(`${key} was updated and reloaded.`));
      expect(request).toHaveBeenCalledWith(
        "/api/control/v1/bot-settings/runtime",
        expect.objectContaining({
          method: "PATCH",
          body: { updates: { [key]: null }, version: 2 },
        }),
      );
      for (const routeKey of routeKeys)
        expect(wrapper.get(`[data-testid="runtime-${routeKey}"]`).element).toHaveProperty(
          "value",
          "",
        );
    },
  );

  it("rebuilds all null route drafts correctly after saving an unrelated setting", async () => {
    stubSettings("local-profile", null);
    const wrapper = await mountSuspended(BotSettingsPage);
    await vi.waitFor(() =>
      expect(wrapper.find('select[data-testid="runtime-chat_provider_profile"]').exists()).toBe(
        true,
      ),
    );
    await wrapper.get('[data-testid="save-runtime-learning_default_enabled"]').trigger("click");
    await vi.waitFor(() =>
      expect(wrapper.text()).toContain("learning_default_enabled was updated and reloaded."),
    );
    await vi.waitFor(() => {
      for (const key of routeKeys)
        expect(wrapper.get(`[data-testid="runtime-${key}"]`).element).toHaveProperty("value", "");
    });
    expect(
      wrapper.get('[data-testid="runtime-raw_message_retention_days"]').element,
    ).toHaveProperty("value", "off");
  });

  it.each(["disabled-provider", "off"])(
    "preserves the genuinely named unavailable profile %s",
    async (name) => {
      const request = stubSettings(name);
      const wrapper = await mountSuspended(BotSettingsPage);
      await vi.waitFor(() =>
        expect(
          wrapper.find('select[data-testid="runtime-summary_provider_profile"]').exists(),
        ).toBe(true),
      );
      const select = wrapper.get('[data-testid="runtime-summary_provider_profile"]');
      expect(select.element).toHaveProperty("value", name);
      expect(select.text()).toContain(`${name} · unavailable or incompatible`);
      await wrapper.get('[data-testid="save-runtime-summary_provider_profile"]').trigger("click");
      await vi.waitFor(() =>
        expect(wrapper.text()).toContain("summary_provider_profile was updated and reloaded."),
      );
      expect(request).toHaveBeenCalledWith(
        "/api/control/v1/bot-settings/runtime",
        expect.objectContaining({
          method: "PATCH",
          body: { updates: { summary_provider_profile: name }, version: 2 },
        }),
      );
    },
  );
});
