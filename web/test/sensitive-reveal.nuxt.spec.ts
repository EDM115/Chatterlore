import { clearNuxtState, useState } from "#app";
import { mountSuspended } from "@nuxt/test-utils/runtime";
import { flushPromises } from "@vue/test-utils";
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

function revealRequest() {
  return vi.fn().mockImplementation(async (url: string) => {
    if (url.endsWith("/auth/dev/step-up")) {
      return {
        step_up: true,
        verified_at: "2026-08-01T12:00:00Z",
        action: "credential:reveal",
      };
    }
    return {
      credential_id: 42,
      value: "sk-sensitive-1234",
      expires_at: "2026-08-01T12:00:30Z",
    };
  });
}

beforeEach(() => {
  clearNuxtState();
  useState("control-plane-session", () => ({
    user: { telegram_user_id: 111 },
  })).value = { user: { telegram_user_id: 111 } };
  useState("control-plane-csrf", () => null).value = "csrf-token";
  useState("control-plane-session-status", () => "idle").value = "authenticated";
  useState("control-plane-session-bootstrapped", () => false).value = true;
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("ProviderCredentialSecret", () => {
  it("completes exact-action development step-up before requesting the secret", async () => {
    const request = revealRequest();
    vi.stubGlobal("$fetch", request);
    const wrapper = await mountSuspended(ProviderCredentialSecret, {
      props: { credential },
    });

    await wrapper.get('[data-testid="reveal-secret"]').trigger("click");
    await flushPromises();

    expect(request.mock.calls.map(([url]) => url)).toEqual([
      "/api/control/v1/auth/dev/step-up",
      "/api/control/v1/providers/primary/credentials/42/reveal",
    ]);
    expect(wrapper.text()).toContain("sk-sensitive-1234");
    wrapper.unmount();
  });

  it("masks by default, copies only explicitly, and clears after 30 seconds", async () => {
    const copy = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText: copy },
    });
    vi.stubGlobal("$fetch", revealRequest());
    const wrapper = await mountSuspended(ProviderCredentialSecret, {
      props: { credential },
    });
    vi.useFakeTimers();

    expect(wrapper.text()).toContain("•••• •••• 1234");
    expect(wrapper.text()).not.toContain("sk-sensitive-1234");
    await wrapper.get('[data-testid="reveal-secret"]').trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("sk-sensitive-1234");
    expect(copy).not.toHaveBeenCalled();
    await wrapper.get('[data-testid="copy-secret"]').trigger("click");
    expect(copy).toHaveBeenCalledWith("sk-sensitive-1234");

    await wrapper.get('[data-testid="mask-secret"]').trigger("click");
    expect(wrapper.text()).toContain("•••• •••• 1234");
    expect(wrapper.text()).not.toContain("sk-sensitive-1234");

    await wrapper.get('[data-testid="reveal-secret"]').trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("sk-sensitive-1234");

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
    vi.stubGlobal("$fetch", revealRequest());
    const wrapper = await mountSuspended(ProviderCredentialSecret, {
      props: { credential },
    });
    await wrapper.get('[data-testid="reveal-secret"]').trigger("click");
    await flushPromises();
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
    await flushPromises();
    useState("control-plane-session").value = {
      user: { telegram_user_id: 222 },
    };
    await wrapper.vm.$nextTick();
    expect(wrapper.text()).not.toContain("sk-sensitive-1234");
    wrapper.unmount();
  });
});
