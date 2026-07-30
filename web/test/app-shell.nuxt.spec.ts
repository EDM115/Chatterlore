import { mountSuspended } from "@nuxt/test-utils/runtime";
import { describe, expect, it } from "vitest";
import ControlPlaneShell from "../app/components/ControlPlaneShell.vue";

describe("ControlPlaneShell", () => {
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

    expect(wrapper.get('nav[aria-label="Primary navigation"]').text()).toContain("Overview");
    expect(wrapper.get('nav[aria-label="Mobile navigation"]').text()).toContain("Sign in");
    expect(wrapper.get("summary").text()).toBe("Menu");
  });
});
