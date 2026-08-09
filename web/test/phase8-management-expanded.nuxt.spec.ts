import { clearNuxtState, useState } from "#app";
import { mountSuspended } from "@nuxt/test-utils/runtime";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import BotSettingsPage from "../app/pages/bot-settings.vue";
import ChatsPage from "../app/pages/chats.vue";

const session = {
  user: {
    telegram_user_id: 111,
    first_name: "Lu",
    last_name: null,
    username: "lu",
    language_code: "en",
    photo_url: null,
    is_premium: null,
  },
  client_kind: "browser",
  authenticated_at: "2026-08-02T12:00:00Z",
  step_up_at: null,
  expires_at: "2026-08-09T12:00:00Z",
  csrf_token: "csrf-token",
  capabilities: ["bot.admin"],
  chat_capabilities: {},
};

beforeEach(() => {
  clearNuxtState();
  useState("control-plane-session").value = session;
  useState("control-plane-session-status").value = "authenticated";
  useState("control-plane-session-bootstrapped").value = true;
  useState("control-plane-csrf").value = "csrf-token";
});

afterEach(() => {
  vi.unstubAllGlobals();
  Reflect.deleteProperty(window, "Telegram");
});

describe("Phase 8 settings and chat reconciliation", () => {
  it("shows approved model/reasoning controls and actionable version-conflict recovery", async () => {
    const request = vi.fn(async (input: string, options?: { method?: string }) => {
      if (input.endsWith("/bot-settings/admins"))
        return [
          {
            telegram_user_id: 111,
            source: "bootstrap",
            version: null,
            granted_by: null,
            granted_at: null,
          },
        ];
      if (input.endsWith("/bot-settings/runtime") && options?.method === "PATCH")
        throw { response: { status: 409 } };
      if (input.endsWith("/bot-settings/runtime"))
        return [
          {
            key: "reasoning_level",
            value: "medium",
            default_value: "medium",
            previous_value: "low",
            updated_by: 111,
            updated_at: "2026-08-02T11:00:00Z",
            version: 2,
          },
        ];
      if (input.endsWith("/query/catalog?scope=all"))
        return {
          scope: "all",
          chat_models: ["gpt-5.6-terra", "gpt-5.4-mini"],
          embedding_models: ["text-embedding-3-small"],
          reasoning_levels: ["low", "medium", "high"],
          profiles: [],
        };
      throw new Error(`Unexpected request: ${input}`);
    });
    vi.stubGlobal("$fetch", request);
    const wrapper = await mountSuspended(BotSettingsPage);
    await vi.waitFor(() => expect(wrapper.text()).toContain("gpt-5.6-terra"));

    expect(wrapper.text()).toContain("gpt-5.4-mini");
    expect(wrapper.text()).toContain("text-embedding-3-small");
    expect(wrapper.text()).toContain("low · medium · high");
    expect(wrapper.text()).toContain("Previous: low");
    await wrapper.get('[data-testid="save-runtime-reasoning_level"]').trigger("click");
    await vi.waitFor(() => expect(wrapper.text()).toContain("changed in another session"));
  });

  it("filters lifecycle states while retaining totals and topic-scope administration", async () => {
    const request = vi.fn(async (input: string) => {
      if (input.endsWith("/chats?limit=100"))
        return {
          items: [
            {
              chat_id: -1001,
              title: "Active group",
              username: "active",
              status: "active",
              version: 1,
              updated_at: "2026-08-02T10:00:00Z",
            },
            {
              chat_id: -1002,
              title: "Paused group",
              username: null,
              status: "paused",
              version: 2,
              updated_at: "2026-08-02T10:00:00Z",
            },
            {
              chat_id: -1003,
              title: "Detached group",
              username: null,
              status: "detached",
              version: 3,
              updated_at: "2026-08-02T10:00:00Z",
            },
          ],
          next_cursor: null,
          has_more: false,
        };
      if (input.endsWith("/chats/-1001/admins")) return [];
      if (input.endsWith("/chats/-1001/settings"))
        return {
          scope: "-1001",
          effective: { retrieval_top_k: 6 },
          inherited: { retrieval_top_k: 6 },
          overrides: {},
          policy: { learning_enabled: true, allow_admins: false },
          version: 0,
        };
      if (input.endsWith("/chats/-1001/settings?thread_id=22"))
        return {
          scope: "-1001:22",
          effective: { retrieval_top_k: 9 },
          inherited: { retrieval_top_k: 6 },
          overrides: { retrieval_top_k: 9 },
          policy: { learning_enabled: true, allow_admins: false },
          version: 1,
        };
      throw new Error(`Unexpected request: ${input}`);
    });
    vi.stubGlobal("$fetch", request);
    const wrapper = await mountSuspended(ChatsPage);
    await vi.waitFor(() => expect(wrapper.text()).toContain("Active group"));

    expect(wrapper.text()).toContain("1 active");
    expect(wrapper.text()).toContain("1 paused");
    expect(wrapper.text()).toContain("1 detached");
    await wrapper.get('[data-testid="chat-status-filter"]').setValue("paused");
    expect(wrapper.text()).toContain("Paused group");
    expect(wrapper.text()).not.toContain("Active group");
    await wrapper.get('[data-testid="chat-status-filter"]').setValue("all");
    await wrapper.get("#thread-id").setValue("22");
    await wrapper.get('[data-testid="load-topic-scope"]').trigger("click");
    await vi.waitFor(() => expect(wrapper.text()).toContain("-1001:22"));
    expect(wrapper.text()).toContain("Inherited: 6");
  });
});
