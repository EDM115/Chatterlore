import { clearNuxtState, useState } from "#app";
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
});

describe("login page", () => {
  it("uses the server OIDC start route in a regular browser", async () => {
    const wrapper = await mountSuspended(LoginPage);

    expect(wrapper.get('a[data-testid="telegram-browser-login"]').attributes("href")).toBe(
      "/auth/telegram/start?return_to=%2F",
    );
  });
});
