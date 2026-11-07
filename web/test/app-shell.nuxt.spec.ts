import { clearNuxtState, useState } from "#app";
import { mountSuspended } from "@nuxt/test-utils/runtime";
import { useAppConfig } from "#imports";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ControlPlaneShell from "~/components/ControlPlaneShell.vue";
import { TELEGRAM_WEB_APP_SCRIPT } from "~~/telegram";

beforeEach(() => clearNuxtState());
afterEach(() => vi.unstubAllGlobals());

function authenticated(clientKind: "browser" | "mini_app") {
  useState("control-plane-session-status").value = "authenticated";
  useState("control-plane-session-bootstrapped").value = true;
  useState("control-plane-csrf").value = "shell-csrf";
  useState("control-plane-session").value = {
    user: {
      telegram_user_id: 111,
      first_name: "Owner",
      last_name: null,
      username: null,
      language_code: null,
      photo_url: null,
      is_premium: null,
    },
    client_kind: clientKind,
    authenticated_at: "2026-11-07T12:00:00Z",
    step_up_at: null,
    expires_at: "2026-11-14T12:00:00Z",
    csrf_token: "shell-csrf",
    capabilities: ["bot.admin"],
    chat_capabilities: {},
  };
}

describe("ControlPlaneShell", () => {
  it.each(["Primary navigation", "Mobile navigation"])(
    "logs out browser sessions from %s",
    async (navigation) => {
      authenticated("browser");
      const request = vi.fn().mockResolvedValue(undefined);
      vi.stubGlobal("$fetch", request);
      const wrapper = await mountSuspended(ControlPlaneShell);
      const menu = wrapper.get("details");
      (menu.element as HTMLDetailsElement).open = true;
      await wrapper
        .get(`nav[aria-label="${navigation}"] button[aria-label="Log out"]`)
        .trigger("click");
      await vi.waitFor(() =>
        expect(useState("control-plane-session-status").value).toBe("anonymous"),
      );
      expect(useState("control-plane-session").value).toBeNull();
      expect(useState("control-plane-csrf").value).toBeNull();
      expect(request).toHaveBeenCalledWith(
        "/api/control/v1/auth/logout",
        expect.objectContaining({
          method: "POST",
          headers: { "X-CSRF-Token": "shell-csrf" },
          credentials: "include",
        }),
      );
      expect((menu.element as HTMLDetailsElement).open).toBe(false);
      expect(wrapper.find('[aria-label="Log out"]').exists()).toBe(false);
    },
  );

  it("retains the browser session and offers retry when logout fails", async () => {
    authenticated("browser");
    vi.stubGlobal("$fetch", vi.fn().mockRejectedValue(new Error("network unavailable")));
    const wrapper = await mountSuspended(ControlPlaneShell);
    await wrapper
      .get('nav[aria-label="Primary navigation"] button[aria-label="Log out"]')
      .trigger("click");
    await vi.waitFor(() =>
      expect(wrapper.get('[role="alert"]').text()).toContain(
        "Log out could not be completed. Please retry.",
      ),
    );
    expect(useState("control-plane-session-status").value).toBe("authenticated");
    expect(wrapper.find('[aria-label="Log out"]').exists()).toBe(true);
  });

  it("does not expose logout in either Mini App navigation", async () => {
    authenticated("mini_app");
    const wrapper = await mountSuspended(ControlPlaneShell);
    expect(wrapper.find('[aria-label="Log out"]').exists()).toBe(false);
    expect(wrapper.get('nav[aria-label="Primary navigation"]').text()).toContain("Dashboard");
    expect(wrapper.get('nav[aria-label="Mobile navigation"]').text()).toContain("Dashboard");
  });

  it("applies the control-plane brand configuration from the Nuxt app directory", () => {
    expect(useAppConfig().ui.colors.primary).toBe("sky");
  });

  it("keeps the main content reachable by keyboard before navigation", async () => {
    const wrapper = await mountSuspended(ControlPlaneShell, {
      slots: { default: "<h1>Workspace overview</h1>" },
    });

    expect(wrapper.get(".skip-link").attributes("href")).toBe("#control-plane-content");
    expect(wrapper.get("#control-plane-content").attributes("tabindex")).toBe("-1");
    expect(wrapper.get("#control-plane-content").text()).toContain("Workspace overview");
  });

  it("exposes distinct accessible desktop and mobile navigation", async () => {
    const wrapper = await mountSuspended(ControlPlaneShell);

    expect(wrapper.get('nav[aria-label="Primary navigation"]').text()).toContain("Sign in");
    expect(wrapper.get('nav[aria-label="Mobile navigation"]').text()).toContain("Sign in");
    expect(wrapper.get("summary").text()).toBe("Menu");
  });

  it("closes the mobile menu after activating a navigation item", async () => {
    const wrapper = await mountSuspended(ControlPlaneShell);
    const menu = wrapper.get("details");
    (menu.element as HTMLDetailsElement).open = true;

    await wrapper.get('nav[aria-label="Mobile navigation"] a').trigger("click");

    expect((menu.element as HTMLDetailsElement).open).toBe(false);
  });

  it("loads the current official Telegram Web App script revision", () => {
    expect(TELEGRAM_WEB_APP_SCRIPT).toBe("https://telegram.org/js/telegram-web-app.js?63");
  });
});
