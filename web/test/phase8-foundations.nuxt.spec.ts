import { clearNuxtState, useState } from "#app";
import { mountSuspended } from "@nuxt/test-utils/runtime";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { defineComponent } from "vue";
import { controlErrorMessage } from "~/composables/useControlFeedback";
import { configureTelegramChrome, syncTelegramClosingConfirmation } from "~/utils/telegramChrome";
import { useControlApi } from "~/composables/useControlApi";
import LoginPage from "~/pages/login.vue";

const ApiHarness = defineComponent({
  setup() {
    return useControlApi();
  },
  template: "<div />",
});

beforeEach(() => {
  clearNuxtState();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("Phase 8 shared interaction foundations", () => {
  it.each([
    [401, "Your Telegram session expired. Sign in again."],
    [403, "Your Telegram account does not have access to this scope."],
    [409, "This record changed in another session. Refresh before retrying."],
    [422, "Check the highlighted values and try again."],
    [429, "Too many requests. Wait a moment before retrying."],
  ])("turns HTTP %s into an actionable accessible message", (status, expected) => {
    expect(controlErrorMessage({ response: { status } })).toBe(expected);
  });

  it("clears protected session state after any API request receives 401", async () => {
    useState("control-plane-session").value = { user: { telegram_user_id: 111 } };
    useState("control-plane-session-status").value = "authenticated";
    useState("control-plane-csrf").value = "csrf-token";
    vi.stubGlobal("$fetch", vi.fn().mockRejectedValue({ response: { status: 401 } }));
    const wrapper = await mountSuspended(ApiHarness);

    await expect(wrapper.vm.request("/stats/overview")).rejects.toBeTruthy();

    expect(useState("control-plane-session").value).toBeNull();
    expect(useState("control-plane-session-status").value).toBe("anonymous");
    expect(useState("control-plane-csrf").value).toBeNull();
  });

  it("configures Telegram chrome with feature detection and reversible callbacks", () => {
    const back = { hide: vi.fn(), offClick: vi.fn(), onClick: vi.fn(), show: vi.fn() };
    const settings = { hide: vi.fn(), offClick: vi.fn(), onClick: vi.fn(), show: vi.fn() };
    const webApp = {
      BackButton: back,
      SettingsButton: settings,
      initData: "signed",
      colorScheme: "dark" as const,
      ready: vi.fn(),
      expand: vi.fn(),
      setBackgroundColor: vi.fn(),
      setBottomBarColor: vi.fn(),
      setHeaderColor: vi.fn(),
    };
    const onBack = vi.fn();
    const onSettings = vi.fn();

    const cleanup = configureTelegramChrome(webApp, {
      canOpenSettings: true,
      isRoot: false,
      onBack,
      onSettings,
    });

    expect(webApp.ready).toHaveBeenCalledOnce();
    expect(webApp.expand).toHaveBeenCalledOnce();
    expect(back.show).toHaveBeenCalledOnce();
    expect(settings.show).toHaveBeenCalledOnce();
    expect(webApp.setHeaderColor).toHaveBeenCalledWith("secondary_bg_color");
    expect(webApp.setBackgroundColor).toHaveBeenCalledWith("bg_color");
    expect(webApp.setBottomBarColor).toHaveBeenCalledWith("bottom_bar_bg_color");
    cleanup();
    expect(back.offClick).toHaveBeenCalledWith(onBack);
    expect(settings.offClick).toHaveBeenCalledWith(onSettings);
  });

  it("enables Telegram close confirmation only while a form is dirty", () => {
    const webApp = {
      enableClosingConfirmation: vi.fn(),
      disableClosingConfirmation: vi.fn(),
    };

    syncTelegramClosingConfirmation(webApp, true);
    syncTelegramClosingConfirmation(webApp, false);

    expect(webApp.enableClosingConfirmation).toHaveBeenCalledOnce();
    expect(webApp.disableClosingConfirmation).toHaveBeenCalledOnce();
  });

  it("explains how to recover when an expired session redirects to login", async () => {
    useState("control-plane-session-status").value = "anonymous";
    useState("control-plane-session-bootstrapped").value = true;
    const wrapper = await mountSuspended(LoginPage, { route: "/login?expired=1" });

    expect(wrapper.get('[role="alert"]').text()).toContain("Your Telegram session expired");
    expect(wrapper.text()).toContain("Sign in again to continue");
  });
});
