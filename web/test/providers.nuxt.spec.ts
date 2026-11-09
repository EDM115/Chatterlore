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
  authenticated_at: "2026-08-01T12:00:00Z",
  step_up_at: null,
  expires_at: "2026-08-08T12:00:00Z",
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

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("providers page", () => {
  it("loads only after bot-admin hydration and exposes explicit paid tests and safe alerts", async () => {
    const request = vi.fn(async (input: string, options?: { method?: string }) => {
      if (input.endsWith("/providers")) {
        return [
          {
            name: "primary",
            provider_kind: "openai_compatible",
            base_url: "https://api.openai.com/v1",
            chat_model: "gpt-6.1-terra",
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
          },
        ];
      }
      if (input.endsWith("/providers/primary/credentials")) {
        return [
          {
            id: 42,
            profile_name: "primary",
            label: "Primary key",
            source: "managed",
            environment_variable: null,
            last_four: "1234",
            fingerprint: "ab12cd34ef56",
            position: 0,
            state: "enabled",
            health_state: "healthy",
            cooldown_until: null,
            last_error_classification: null,
            success_count: 5,
            failure_count: 1,
            last_success_at: "2026-08-01T12:00:00Z",
            last_failure_at: "2026-08-01T11:00:00Z",
            version: 4,
            revealable: true,
            active: true,
          },
        ];
      }
      if (input.endsWith("/providers/primary/pool")) {
        return {
          profile_name: "primary",
          profile_revision: 1,
          active_credential_fingerprint: "ab12cd34ef56",
          active_credential_label: "Primary key",
          health_epoch: 3,
          total_credentials: 1,
          eligible_credentials: 1,
          cooldown_credentials: 0,
          disabled_credentials: 0,
          last_exhausted_at: null,
          last_recovered_at: "2026-08-01T12:00:00Z",
        };
      }
      if (input.includes("/alerts?limit=")) {
        return {
          items: [
            {
              id: 7,
              parent_intent_id: null,
              alert_type: "credential_recovered",
              profile_name: "primary",
              credential_fingerprint: "ab12cd34ef56",
              credential_label: "Primary key",
              health_epoch: 3,
              recipient_telegram_id: null,
              state: "expanded",
              attempts: 0,
              next_attempt_at: null,
              created_at: "2026-08-01T12:00:00Z",
              last_attempt_at: null,
              delivered_at: null,
              error_classification: null,
            },
          ],
          next_cursor: null,
          has_more: false,
        };
      }
      if (input.endsWith("/providers/primary/credentials/42/test")) {
        return {
          ok: true,
          mode: "chat",
          model: "gpt-6.1-terra",
          dimensions: null,
          managed_profile_revision: 1,
          credential_fingerprint: "ab12cd34ef56",
          credential_label: "Primary key",
          attempted_credential_count: 1,
          same_key_attempt_count: 1,
          rotation_count: 0,
          final_classification: "success",
          warning: "This manual check may incur provider usage charges.",
        };
      }
      if (input.endsWith("/alerts/7/retry") && options?.method === "POST") {
        return { state: "pending" };
      }
      throw new Error(`Unexpected request: ${input}`);
    });
    vi.stubGlobal("$fetch", request);

    const wrapper = await mountSuspended(ProvidersPage);
    await vi.waitFor(() => expect(wrapper.text()).toContain("Primary key"));

    expect(wrapper.text()).toContain("Provider operations may incur usage charges");
    expect(wrapper.text()).toContain("Local model checks may download model files");
    expect(wrapper.text()).toContain("credential_recovered");
    expect(wrapper.text()).not.toContain("sk-sensitive");
    await wrapper.get('[data-testid="test-chat-42"]').trigger("click");
    await vi.waitFor(() => expect(wrapper.text()).toContain("gpt-6.1-terra passed"));
    expect(request).toHaveBeenCalledWith(
      "/api/control/v1/providers/primary/credentials/42/test",
      expect.objectContaining({ body: { mode: "chat" }, method: "POST" }),
    );
    await wrapper.get('[data-testid="retry-alert-7"]').trigger("click");
    await vi.waitFor(() =>
      expect(request).toHaveBeenCalledWith(
        "/api/control/v1/alerts/7/retry",
        expect.objectContaining({ method: "POST" }),
      ),
    );
  });

  it("shows an environment bootstrap profile without requesting managed pool state", async () => {
    const request = vi.fn(async (input: string) => {
      if (input.endsWith("/providers")) {
        return [
          {
            name: "default",
            provider_kind: "fake",
            base_url: "https://api.openai.com/v1",
            chat_model: "fake-chat",
            embedding_model: "fake-embedding",
            chat_capable: true,
            embedding_capable: true,
            local: false,
            enabled: true,
            source: "environment",
          },
        ];
      }
      if (input.endsWith("/providers/default/credentials")) return [];
      if (input.includes("/alerts?limit=")) {
        return { items: [], next_cursor: null, has_more: false };
      }
      throw new Error(`Unexpected request: ${input}`);
    });
    vi.stubGlobal("$fetch", request);

    const wrapper = await mountSuspended(ProvidersPage);
    await vi.waitFor(() => expect(wrapper.text()).toContain("Environment bootstrap profiles"));

    expect(wrapper.text()).not.toContain("Provider operations could not be loaded.");
    expect(request.mock.calls.flatMap((call) => call).join(" ")).not.toContain(
      "/providers/default/pool",
    );
  });
});
