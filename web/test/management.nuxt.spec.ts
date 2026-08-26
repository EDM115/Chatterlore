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
  client_kind: "mini_app",
  authenticated_at: "2026-08-01T12:00:00Z",
  step_up_at: null,
  expires_at: "2026-08-08T12:00:00Z",
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

describe("Phase 6 management pages", () => {
  it("manages durable bot administrators and safe runtime settings", async () => {
    const request = vi.fn(async (input: string, options?: { body?: unknown; method?: string }) => {
      if (input.endsWith("/bot-settings/admins") && options?.method === "POST") {
        return {
          telegram_user_id: 222,
          source: "managed",
          version: 1,
          granted_by: 111,
          granted_at: "2026-08-01T12:00:00Z",
        };
      }
      if (input.endsWith("/bot-settings/admins")) {
        return [
          {
            telegram_user_id: 111,
            source: "bootstrap",
            version: null,
            granted_by: null,
            granted_at: null,
          },
        ];
      }
      if (input.endsWith("/bot-settings/runtime") && options?.method === "PATCH") {
        return {
          items: [
            {
              key: "retrieval_top_k",
              value: 9,
              default_value: 6,
              previous_value: 6,
              updated_by: 111,
              updated_at: "2026-08-01T12:00:00Z",
              version: 1,
            },
          ],
          version: 1,
          audit_event_id: 12,
        };
      }
      if (input.endsWith("/bot-settings/runtime")) {
        return [
          {
            key: "retrieval_top_k",
            value: 6,
            default_value: 6,
            previous_value: null,
            updated_by: null,
            updated_at: null,
            version: null,
          },
        ];
      }
      throw new Error(`Unexpected request: ${input}`);
    });
    vi.stubGlobal("$fetch", request);

    const wrapper = await mountSuspended(BotSettingsPage);
    await vi.waitFor(() => expect(wrapper.text()).toContain("Bootstrap owner"));
    await wrapper.get('[data-testid="new-bot-admin"]').setValue("222");
    await wrapper.get('[data-testid="add-bot-admin"]').trigger("click");
    expect(request).toHaveBeenCalledWith(
      "/api/control/v1/bot-settings/admins",
      expect.objectContaining({ body: { telegram_user_id: 222 }, method: "POST" }),
    );
    await wrapper.get('[data-testid="runtime-retrieval_top_k"]').setValue("9");
    await wrapper.get('[data-testid="save-runtime-retrieval_top_k"]').trigger("click");
    expect(request).toHaveBeenCalledWith(
      "/api/control/v1/bot-settings/runtime",
      expect.objectContaining({
        body: { updates: { retrieval_top_k: 9 }, version: 0 },
        method: "PATCH",
      }),
    );
    expect(wrapper.text()).not.toContain("openai_api_key");
  });

  it("shows Mini App step-up rejection instead of making Remove appear inert", async () => {
    useState("control-plane-session").value = { ...session, client_kind: "mini_app" };
    window.Telegram = {
      WebApp: {
        initData: "signed-init-data",
        colorScheme: "dark",
        ready: vi.fn(),
        expand: vi.fn(),
      },
    };
    const envelope = {
      error: {
        code: "mini_app_step_up_rejected",
        message: "Telegram Mini App identity could not be verified.",
      },
    };
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
          {
            telegram_user_id: 222,
            source: "managed",
            version: 1,
            granted_by: 111,
            granted_at: "2026-08-01T12:00:00Z",
          },
        ];
      if (input.endsWith("/bot-settings/runtime")) return [];
      if (input.endsWith("/chats?limit=100"))
        return { items: [], next_cursor: null, has_more: false };
      if (input.endsWith("/auth/step-up/mini-app"))
        throw { data: envelope, response: { status: 401, _data: envelope } };
      throw new Error(`Unexpected request: ${input} ${options?.method ?? "GET"}`);
    });
    vi.stubGlobal("$fetch", request);
    const wrapper = await mountSuspended(BotSettingsPage);
    await vi.waitFor(() => expect(wrapper.text()).toContain("Managed grant v1"));
    const remove = wrapper.findAll("button").find((button) => button.text() === "Remove");

    await remove?.trigger("click");

    await vi.waitFor(() =>
      expect(wrapper.text()).toContain(
        "Telegram verification could not be completed in this Mini App. Open the control plane in a regular browser to retry securely.",
      ),
    );
    expect(request).not.toHaveBeenCalledWith(
      expect.stringContaining("/bot-settings/admins/222"),
      expect.objectContaining({ method: "DELETE" }),
    );
  });

  it("uses Telegram requestChat and polls the bound enrollment request", async () => {
    const requestChat = vi.fn((_id: string, callback?: (sent: boolean) => void) =>
      callback?.(true),
    );
    window.Telegram = {
      WebApp: {
        initData: "signed-init-data",
        colorScheme: "dark",
        version: "10.2",
        isVersionAtLeast: () => true,
        ready: () => undefined,
        expand: () => undefined,
        requestChat,
      },
    };
    const request = vi.fn(async (input: string, options?: { body?: unknown; method?: string }) => {
      if (input.endsWith("/chats/enrollment-requests") && options?.method === "POST") {
        return {
          request_id: 77,
          status: "pending",
          delivery: "mini_app",
          expires_at: "2099-08-01T12:02:00Z",
          prepared_button_id: "prepared-request-chat",
          chat_id: null,
          error_code: null,
        };
      }
      if (input.endsWith("/chats/enrollment-requests/77")) {
        return {
          request_id: 77,
          status: "completed",
          delivery: "mini_app",
          expires_at: "2099-08-01T12:02:00Z",
          prepared_button_id: "prepared-request-chat",
          chat_id: -1001,
          error_code: null,
        };
      }
      if (input.endsWith("/chats?limit=100"))
        return { items: [], next_cursor: null, has_more: false };
      throw new Error(`Unexpected request: ${input}`);
    });
    vi.stubGlobal("$fetch", request);

    const wrapper = await mountSuspended(ChatsPage);
    await vi.waitFor(() => expect(wrapper.text()).toContain("Add a Telegram group"));
    await wrapper.get('[data-testid="enroll-chat"]').trigger("click");
    await vi.waitFor(() =>
      expect(requestChat).toHaveBeenCalledWith("prepared-request-chat", expect.any(Function)),
    );
    await vi.waitFor(() => expect(wrapper.text()).toContain("Telegram chat -1001 enrolled"));
  });

  it("falls back to a bot-DM keyboard outside a current Telegram client", async () => {
    const request = vi.fn(async (input: string, options?: { body?: unknown; method?: string }) => {
      if (input.endsWith("/chats/enrollment-requests") && options?.method === "POST") {
        expect(options.body).toEqual({ delivery: "telegram_dm" });
        return {
          request_id: 88,
          status: "pending",
          delivery: "telegram_dm",
          expires_at: "2099-08-01T12:02:00Z",
          prepared_button_id: "prepared-fallback",
          chat_id: null,
          error_code: null,
        };
      }
      if (input.endsWith("/chats/enrollment-requests/88")) {
        return {
          request_id: 88,
          status: "pending",
          delivery: "telegram_dm",
          expires_at: "2099-08-01T12:02:00Z",
          prepared_button_id: "prepared-fallback",
          chat_id: null,
          error_code: null,
        };
      }
      if (input.endsWith("/chats?limit=100"))
        return { items: [], next_cursor: null, has_more: false };
      throw new Error(`Unexpected request: ${input}`);
    });
    vi.stubGlobal("$fetch", request);

    const wrapper = await mountSuspended(ChatsPage);
    await wrapper.get('[data-testid="enroll-chat"]').trigger("click");
    await vi.waitFor(() =>
      expect(wrapper.text()).toContain("selection button was sent to your bot DM"),
    );
  });
});
