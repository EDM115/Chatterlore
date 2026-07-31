import { clearNuxtState } from "#app";
import { mountSuspended } from "@nuxt/test-utils/runtime";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { defineComponent } from "vue";
import LoginPage from "../app/pages/login.vue";
import { useControlSession } from "../app/composables/useControlSession";

const Harness = defineComponent({
  setup() {
    return useControlSession();
  },
  template: "<div />",
});

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

beforeEach(() => {
  clearNuxtState();
});

afterEach(() => {
  vi.unstubAllGlobals();
  delete window.Telegram;
});

describe("useControlSession", () => {
  it("restores a regular-browser session with credentialed same-origin requests", async () => {
    const request = vi.fn().mockResolvedValue(sessionPayload);
    vi.stubGlobal("$fetch", request);
    const wrapper = await mountSuspended(Harness);

    await wrapper.vm.bootstrap();

    expect(request).toHaveBeenCalledWith("/api/control/v1/session", {
      credentials: "include",
      headers: {},
      method: "GET",
      timeout: 5_000,
    });
    expect(wrapper.vm.status).toBe("authenticated");
    expect(wrapper.vm.user.telegram_user_id).toBe(887758628);
    expect(wrapper.vm.hasCapability("bot.admin")).toBe(true);
  });

  it("exchanges signed Mini App init data once instead of falling back to browser auth", async () => {
    window.Telegram = {
      WebApp: {
        initData: "signed-init-data",
        colorScheme: "dark",
        ready: vi.fn(),
        expand: vi.fn(),
      },
    };
    const request = vi.fn().mockResolvedValue({
      ...sessionPayload,
      client_kind: "mini_app",
    });
    vi.stubGlobal("$fetch", request);
    const wrapper = await mountSuspended(Harness);

    await wrapper.vm.bootstrap();
    await wrapper.vm.bootstrap();

    expect(request).toHaveBeenCalledTimes(1);
    expect(request).toHaveBeenCalledWith("/api/control/v1/auth/mini-app", {
      body: { init_data: "signed-init-data" },
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      method: "POST",
      timeout: 5_000,
    });
    expect(wrapper.vm.clientKind).toBe("mini_app");
  });
});

describe("login page", () => {
  it("uses the server OIDC start route in a regular browser", async () => {
    const wrapper = await mountSuspended(LoginPage);

    expect(wrapper.get('a[data-testid="telegram-browser-login"]').attributes("href")).toBe(
      "/auth/telegram/start?return_to=%2F",
    );
  });
});
