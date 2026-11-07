import { clearNuxtState, useState } from "#app";
import { mountSuspended } from "@nuxt/test-utils/runtime";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ProvidersPage from "~/pages/providers.vue";

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
  client_kind: "browser",
  authenticated_at: "2026-08-02T12:00:00Z",
  step_up_at: null,
  expires_at: "2026-08-09T12:00:00Z",
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

afterEach(() => vi.unstubAllGlobals());

describe("Phase 8 provider administration", () => {
  it("creates a managed profile and key, then atomically replaces the secret without exposing it", async () => {
    let profileCreated = false;
    let credential: Record<string, unknown> | null = null;
    const profile = {
      name: "primary",
      provider_kind: "openai_compatible",
      base_url: "https://api.openai.com/v1",
      chat_model: "gpt-5.6-terra",
      embedding_model: "text-embedding-3-small",
      chat_capable: true,
      embedding_capable: true,
      reasoning_capable: true,
      local: false,
      redact_before_ai: true,
      redaction_patterns: [],
      fake_embedding_dimensions: 64,
      enabled: true,
      source: "managed",
      version: 1,
    };
    const request = vi.fn(
      async (input: string, options?: { body?: Record<string, unknown>; method?: string }) => {
        if (input.endsWith("/providers") && options?.method === "POST") {
          expect(options.body).toMatchObject({
            name: "primary",
            base_url: "https://provider.test/v1",
            chat_model: "gpt-5.6-terra",
            reasoning_capable: true,
          });
          profileCreated = true;
          return profile;
        }
        if (input.endsWith("/providers")) return profileCreated ? [profile] : [];
        if (input.includes("/alerts?limit="))
          return { items: [], next_cursor: null, has_more: false };
        if (input.endsWith("/providers/primary/credentials") && options?.method === "POST") {
          expect(options.body).toEqual({ label: "Main key", value: "sk-new-secret" });
          credential = {
            id: 42,
            profile_name: "primary",
            label: "Main key",
            source: "managed",
            environment_variable: null,
            last_four: "cret",
            fingerprint: "fingerprint-one",
            position: 0,
            state: "enabled",
            health_state: "unknown",
            cooldown_until: null,
            last_error_classification: null,
            success_count: 0,
            failure_count: 0,
            last_success_at: null,
            last_failure_at: null,
            version: 1,
            revealable: true,
            active: false,
          };
          return credential;
        }
        if (input.endsWith("/providers/primary/credentials")) return credential ? [credential] : [];
        if (input.endsWith("/providers/primary/pool"))
          return {
            profile_name: "primary",
            profile_revision: 1,
            active_credential_fingerprint: null,
            active_credential_label: null,
            health_epoch: 0,
            total_credentials: credential ? 1 : 0,
            eligible_credentials: credential ? 1 : 0,
            cooldown_credentials: 0,
            disabled_credentials: 0,
            last_exhausted_at: null,
            last_recovered_at: null,
          };
        if (input.endsWith("/auth/dev/step-up") && options?.method === "POST")
          return {
            step_up: true,
            verified_at: "2026-08-02T12:01:00Z",
            action: options.body?.action,
          };
        if (
          input.endsWith("/providers/primary/credentials/42/replace") &&
          options?.method === "POST"
        ) {
          expect(options.body).toEqual({
            version: 1,
            label: "Rotated key",
            value: "sk-rotated-secret",
          });
          credential = {
            ...credential,
            label: "Rotated key",
            last_four: "cret",
            fingerprint: "fingerprint-two",
            version: 2,
          };
          return credential;
        }
        throw new Error(`Unexpected request: ${input}`);
      },
    );
    vi.stubGlobal("$fetch", request);
    const wrapper = await mountSuspended(ProvidersPage);
    await vi.waitFor(() => expect(wrapper.text()).toContain("Create managed profile"));

    await wrapper.get('[data-testid="new-profile-name"]').setValue("primary");
    await wrapper.get('[data-testid="new-profile-base-url"]').setValue("https://provider.test/v1");
    await wrapper.get('[data-testid="create-profile"]').trigger("click");
    await vi.waitFor(() => expect(wrapper.text()).toContain("No credentials are configured"));

    await wrapper.get('[data-testid="new-credential-label"]').setValue("Main key");
    await wrapper.get('[data-testid="new-credential-value"]').setValue("sk-new-secret");
    await wrapper.get('[data-testid="create-credential"]').trigger("click");
    await vi.waitFor(() => expect(wrapper.text()).toContain("Main key"));
    expect(wrapper.text()).not.toContain("sk-new-secret");

    await wrapper.get('[data-testid="replace-credential-label-42"]').setValue("Rotated key");
    await wrapper.get('[data-testid="replace-credential-value-42"]').setValue("sk-rotated-secret");
    await wrapper.get('[data-testid="replace-credential-42"]').trigger("click");
    await vi.waitFor(() => expect(wrapper.text()).toContain("Rotated key"));
    expect(wrapper.text()).not.toContain("sk-rotated-secret");
    expect(request).toHaveBeenCalledWith(
      "/api/control/v1/providers/primary/credentials/42/replace",
      expect.objectContaining({ method: "POST" }),
    );
  });
});
