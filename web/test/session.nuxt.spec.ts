import { clearNuxtState, useState } from "#app";
import { mountSuspended } from "@nuxt/test-utils/runtime";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { defineComponent } from "vue";
import LoginPage from "~/pages/login.vue";
import { useControlSession } from "~/composables/useControlSession";
import { TELEGRAM_WEB_APP_READY_EVENT, waitForTelegramInitData } from "~/utils/telegramWebApp";

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
  window.history.replaceState(null, "", "/");
});

describe("useControlSession", () => {
  it("reuses only the exact unexpired server action after a browser verification return", async () => {
    const request = vi.fn();
    vi.stubGlobal("$fetch", request);
    const wrapper = await mountSuspended(Harness);
    wrapper.vm.session = {
      ...sessionPayload,
      step_up_action: "embedding:adopt",
      step_up_expires_at: new Date(Date.now() + 60_000).toISOString(),
    };
    request.mockClear();
    expect(await wrapper.vm.ensureStepUp("embedding:adopt", "/embedding-indexes")).toBe(true);
    expect(request).not.toHaveBeenCalled();
  });

  it.each(["expired", "other-action"])("does not reuse %s browser proof", async (kind) => {
    const request = vi.fn().mockResolvedValue({ step_up: false });
    vi.stubGlobal("$fetch", request);
    const wrapper = await mountSuspended(Harness);
    wrapper.vm.session = {
      ...sessionPayload,
      step_up_action: kind === "other-action" ? "credential:reveal" : "embedding:adopt",
      step_up_expires_at: new Date(
        Date.now() + (kind === "expired" ? -60_000 : 60_000),
      ).toISOString(),
    };
    expect(await wrapper.vm.ensureStepUp("embedding:adopt", "/embedding-indexes")).toBe(false);
    expect(request).toHaveBeenCalledWith(
      "/api/control/v1/auth/dev/step-up",
      expect.objectContaining({ body: { action: "embedding:adopt" } }),
    );
  });

  it.each([
    ["Authenticated user", [], {}],
    ["Scoped administrator", [], { "-1004148019329": ["chat.admin"] }],
    ["Bot administrator", ["bot.admin"], {}],
  ])(
    "labels an authenticated session from its effective authorization as %s",
    async (expected, capabilities, chatCapabilities) => {
      const wrapper = await mountSuspended(Harness);
      wrapper.vm.session = {
        ...sessionPayload,
        capabilities,
        chat_capabilities: chatCapabilities,
      };
      wrapper.vm.status = "authenticated";
      await wrapper.vm.$nextTick();

      expect(wrapper.vm.accessLabel).toBe(expected);
    },
  );

  it("waits for Telegram launch data before its SDK script element exists", async () => {
    document
      .querySelectorAll('script[src^="https://telegram.org/js/telegram-web-app.js"]')
      .forEach((script) => script.remove());
    window.history.replaceState(null, "", "/#tgWebAppData=placeholder");

    const pending = waitForTelegramInitData();
    await Promise.resolve();
    window.Telegram = {
      WebApp: {
        initData: "early-launch-data",
        colorScheme: "dark",
        ready: vi.fn(),
        expand: vi.fn(),
      },
    };
    window.dispatchEvent(new Event(TELEGRAM_WEB_APP_READY_EVENT));

    expect(await pending).toBe("early-launch-data");
  });

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

  it("reuses the exact Mini App proof accepted at login when the SDK value later diverges", async () => {
    window.Telegram = {
      WebApp: {
        initData: "accepted-signed-init-data",
        colorScheme: "dark",
        ready: vi.fn(),
        expand: vi.fn(),
      },
    };
    const request = vi
      .fn()
      .mockResolvedValueOnce({ ...sessionPayload, client_kind: "mini_app" })
      .mockResolvedValueOnce({
        step_up: true,
        verified_at: "2026-08-01T12:00:00Z",
        action: "bot_admin:delete",
      });
    vi.stubGlobal("$fetch", request);
    const wrapper = await mountSuspended(Harness);

    expect(await wrapper.vm.bootstrap()).toBe(true);
    window.Telegram.WebApp.initData = "later%malformed-sdk-value";
    expect(await wrapper.vm.ensureStepUp("bot_admin:delete", "/bot-settings")).toBe(true);

    expect(request).toHaveBeenNthCalledWith(2, "/api/control/v1/auth/step-up/mini-app", {
      body: { action: "bot_admin:delete", init_data: "accepted-signed-init-data" },
      credentials: "include",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": "csrf-token" },
      method: "POST",
      timeout: 5_000,
    });
  });

  it("waits for late Telegram SDK init data before its head script exists", async () => {
    window.history.replaceState(null, "", "/#tgWebAppData=placeholder");
    const request = vi.fn().mockResolvedValue({
      ...sessionPayload,
      client_kind: "mini_app",
    });
    vi.stubGlobal("$fetch", request);
    const wrapper = await mountSuspended(Harness);

    const bootstrapping = wrapper.vm.bootstrap();
    await Promise.resolve();
    window.Telegram = {
      WebApp: {
        initData: "late-signed-init-data",
        colorScheme: "dark",
        ready: vi.fn(),
        expand: vi.fn(),
      },
    };
    window.dispatchEvent(new Event("chatterlore:telegram-web-app-ready"));

    expect(await bootstrapping).toBe(true);
    expect(request).toHaveBeenCalledTimes(1);
    expect(request).toHaveBeenCalledWith("/api/control/v1/auth/mini-app", {
      body: { init_data: "late-signed-init-data" },
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      method: "POST",
      timeout: 5_000,
    });
  });

  it("uses development login only after an exact anonymous session response", async () => {
    const request = vi
      .fn()
      .mockRejectedValueOnce({ response: { status: 401 } })
      .mockResolvedValueOnce(sessionPayload);
    vi.stubGlobal("$fetch", request);
    const wrapper = await mountSuspended(Harness);

    expect(await wrapper.vm.bootstrap()).toBe(true);

    expect(request).toHaveBeenNthCalledWith(1, "/api/control/v1/session", {
      credentials: "include",
      headers: {},
      method: "GET",
      timeout: 5_000,
    });
    expect(request).toHaveBeenNthCalledWith(2, "/api/control/v1/auth/dev", {
      credentials: "include",
      headers: {},
      method: "POST",
      timeout: 5_000,
    });
    expect(wrapper.vm.status).toBe("authenticated");
  });

  it("does not hide a non-authentication session failure behind development login", async () => {
    const request = vi.fn().mockRejectedValue({ response: { status: 503 } });
    vi.stubGlobal("$fetch", request);
    const wrapper = await mountSuspended(Harness);

    expect(await wrapper.vm.bootstrap()).toBe(false);

    expect(request).toHaveBeenCalledTimes(1);
    expect(wrapper.vm.status).toBe("error");
    expect(wrapper.vm.error).toBe("The control-plane session could not be loaded.");
  });

  it("marks the exact sensitive action through development step-up", async () => {
    useState("control-plane-session", () => null).value = sessionPayload;
    useState("control-plane-csrf", () => null).value = "csrf-token";
    const request = vi.fn().mockResolvedValue({
      step_up: true,
      verified_at: "2026-08-01T12:00:00Z",
      action: "credential:reveal",
    });
    vi.stubGlobal("$fetch", request);
    const wrapper = await mountSuspended(Harness);

    expect(await wrapper.vm.ensureStepUp("credential:reveal", "/providers")).toBe(true);

    expect(request).toHaveBeenCalledWith("/api/control/v1/auth/dev/step-up", {
      body: { action: "credential:reveal" },
      credentials: "include",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": "csrf-token" },
      method: "POST",
      timeout: 5_000,
    });
    expect(wrapper.vm.session.step_up_at).toBe("2026-08-01T12:00:00Z");
  });

  it("keeps signed Mini App step-up ahead of the development shortcut", async () => {
    window.Telegram = {
      WebApp: {
        initData: "signed-init-data",
        colorScheme: "dark",
        ready: vi.fn(),
        expand: vi.fn(),
      },
    };
    useState("control-plane-session", () => null).value = {
      ...sessionPayload,
      client_kind: "mini_app",
    };
    useState("control-plane-csrf", () => null).value = "csrf-token";
    const request = vi.fn().mockResolvedValue({
      step_up: true,
      verified_at: "2026-08-01T12:00:00Z",
      action: "credential:reveal",
    });
    vi.stubGlobal("$fetch", request);
    const wrapper = await mountSuspended(Harness);

    expect(await wrapper.vm.ensureStepUp("credential:reveal", "/providers")).toBe(true);

    expect(request).toHaveBeenCalledWith("/api/control/v1/auth/step-up/mini-app", {
      body: { action: "credential:reveal", init_data: "signed-init-data" },
      credentials: "include",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": "csrf-token" },
      method: "POST",
      timeout: 5_000,
    });
  });

  it("keeps the session and offers secure browser fallback when Mini App step-up fails", async () => {
    window.Telegram = {
      WebApp: {
        initData: "stale-signed-init-data",
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
    vi.stubGlobal(
      "$fetch",
      vi.fn().mockRejectedValue({
        data: envelope,
        response: { status: 401, _data: envelope },
      }),
    );
    const wrapper = await mountSuspended(Harness);
    wrapper.vm.session = { ...sessionPayload, client_kind: "mini_app" };
    wrapper.vm.status = "authenticated";
    useState("control-plane-csrf").value = "csrf-token";

    expect(await wrapper.vm.ensureStepUp("bot_admin:delete", "/bot-settings")).toBe(false);

    expect(wrapper.vm.status).toBe("authenticated");
    expect(wrapper.vm.session.user.telegram_user_id).toBe(887758628);
    expect(wrapper.vm.error).toBe(
      "Telegram verification could not be completed in this Mini App. Open the control plane in a regular browser to retry securely.",
    );
  });
});

describe("login page", () => {
  it("uses the server OIDC start route in a regular browser", async () => {
    const wrapper = await mountSuspended(LoginPage);

    expect(wrapper.get('a[data-testid="telegram-browser-login"]').attributes("href")).toBe(
      "/auth/telegram/start?return_to=%2F",
    );
  });

  it("preserves a bounded direct-route destination in the OIDC start link", async () => {
    const wrapper = await mountSuspended(LoginPage, {
      route: "/login?return_to=%2Fquery",
    });

    expect(wrapper.get('a[data-testid="telegram-browser-login"]').attributes("href")).toBe(
      "/auth/telegram/start?return_to=%2Fquery",
    );
  });

  it("drops an oversized Mini App fallback destination before starting OIDC", async () => {
    const oversized = `/${"a".repeat(512)}`;
    const wrapper = await mountSuspended(LoginPage, {
      route: `/login?return_to=${encodeURIComponent(oversized)}`,
    });

    expect(wrapper.get('a[data-testid="telegram-browser-login"]').attributes("href")).toBe(
      "/auth/telegram/start?return_to=%2F",
    );
  });
});
