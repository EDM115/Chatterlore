import { clearNuxtState, useState } from "#app";
import { mountSuspended } from "@nuxt/test-utils/runtime";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ProviderCredentialSecret from "../app/components/ProviderCredentialSecret.vue";

const credential = {
  id: 42,
  profile_name: "primary",
  label: "Main",
  source: "managed",
  environment_variable: null,
  last_four: "1234",
  fingerprint: "fingerprint",
  position: 0,
  state: "enabled",
  cooldown_until: null,
  last_success_at: null,
  last_failure_at: null,
  version: 1,
  revealable: true,
};

beforeEach(() => {
  clearNuxtState();
  useState("control-plane-session", () => ({
    user: { telegram_user_id: 111 },
  })).value = { user: { telegram_user_id: 111 } };
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("ProviderCredentialSecret", () => {
  it("masks by default, copies only explicitly, and clears after 30 seconds", async () => {
    const copy = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText: copy },
    });
    vi.stubGlobal(
      "$fetch",
      vi.fn().mockResolvedValue({
        credential_id: 42,
        value: "sk-sensitive-1234",
        expires_at: "2026-08-01T12:00:30Z",
      }),
    );
    const wrapper = await mountSuspended(ProviderCredentialSecret, {
      props: { credential },
    });
    vi.useFakeTimers();

    expect(wrapper.text()).toContain("•••• •••• 1234");
    expect(wrapper.text()).not.toContain("sk-sensitive-1234");
    await wrapper.get('[data-testid="reveal-secret"]').trigger("click");
    expect(wrapper.text()).toContain("sk-sensitive-1234");
    expect(copy).not.toHaveBeenCalled();
    await wrapper.get('[data-testid="copy-secret"]').trigger("click");
    expect(copy).toHaveBeenCalledWith("sk-sensitive-1234");

    await vi.advanceTimersByTimeAsync(30_000);
    expect(wrapper.text()).toContain("•••• •••• 1234");
    expect(wrapper.text()).not.toContain("sk-sensitive-1234");
    wrapper.unmount();
  });

  it("clears on page hiding and session identity changes", async () => {
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText: vi.fn() },
    });
    vi.stubGlobal(
      "$fetch",
      vi.fn().mockResolvedValue({
        credential_id: 42,
        value: "sk-sensitive-1234",
        expires_at: "2026-08-01T12:00:30Z",
      }),
    );
    const wrapper = await mountSuspended(ProviderCredentialSecret, {
      props: { credential },
    });
    await wrapper.get('[data-testid="reveal-secret"]').trigger("click");
    expect(wrapper.text()).toContain("sk-sensitive-1234");

    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      value: "hidden",
    });
    document.dispatchEvent(new Event("visibilitychange"));
    await wrapper.vm.$nextTick();
    expect(wrapper.text()).not.toContain("sk-sensitive-1234");

    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      value: "visible",
    });
    await wrapper.get('[data-testid="reveal-secret"]').trigger("click");
    useState("control-plane-session").value = {
      user: { telegram_user_id: 222 },
    };
    await wrapper.vm.$nextTick();
    expect(wrapper.text()).not.toContain("sk-sensitive-1234");
    wrapper.unmount();
  });
});
