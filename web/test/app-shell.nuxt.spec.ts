import { mountSuspended } from "@nuxt/test-utils/runtime";
import { useAppConfig } from "#imports";
import { describe, expect, it } from "vitest";
import ControlPlaneShell from "~/components/ControlPlaneShell.vue";
import { TELEGRAM_WEB_APP_SCRIPT } from "~~/telegram";

describe("ControlPlaneShell", () => {
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
