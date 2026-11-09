import { clearNuxtState, useState } from "#app";
import { mountSuspended } from "@nuxt/test-utils/runtime";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import OperationsPage from "~/pages/operations.vue";

const health = {
  generated_at: "2026-11-09T12:00:00Z",
  checks: ["local-model-readiness", "provider-profile"],
  provider_calls_performed: false,
  runtime: { schema_revision: "0019", uptime_seconds: 60 },
  storage: { database_bytes: 1024, memory_files: 0 },
  providers: { profiles: 2, credentials: { eligible: 0, total: 0 } },
  alerts: { states: {} },
};
const profile = {
  name: "local",
  provider_kind: "local",
  base_url: "",
  chat_model: "example/instruct",
  embedding_model: "example/embed",
  chat_capable: true,
  embedding_capable: true,
  reasoning_capable: false,
  local: true,
  redact_before_ai: true,
  redaction_patterns: [],
  fake_embedding_dimensions: 64,
  enabled: true,
  source: "managed",
  version: 1,
  api_dialect: "generic",
  auth_mode: "none",
  local_chat_options: {},
  local_embedding_options: {},
};

beforeEach(() => {
  clearNuxtState();
  useState("control-plane-session").value = {
    user: { telegram_user_id: 111, first_name: "Owner" },
    client_kind: "browser",
    csrf_token: "csrf-token",
    capabilities: ["bot.admin"],
    chat_capabilities: {},
  };
  useState("control-plane-session-status").value = "authenticated";
  useState("control-plane-session-bootstrapped").value = true;
  useState("control-plane-csrf").value = "csrf-token";
});
afterEach(() => vi.unstubAllGlobals());

describe("owner readiness controls", () => {
  it("allows global provider diagnostics before any managed chat is enrolled", async () => {
    vi.stubGlobal(
      "$fetch",
      vi.fn(async (input: string) => {
        if (input.endsWith("/operations/health")) return health;
        if (input.endsWith("/providers"))
          return [
            {
              ...profile,
              name: "remote",
              provider_kind: "openai_compatible",
              base_url: "https://provider.example/v1",
              local: false,
              auth_mode: "api_key",
            },
          ];
        if (input.endsWith("/chats?limit=100"))
          return { items: [], next_cursor: null, has_more: false };
        throw new Error(`Unexpected request: ${input}`);
      }),
    );
    const wrapper = await mountSuspended(OperationsPage);
    await vi.waitFor(() =>
      expect(
        wrapper.get('[data-testid="check-provider-profile"]').attributes("disabled"),
      ).toBeUndefined(),
    );
    expect(wrapper.text()).toContain("remote");
    expect(wrapper.text()).not.toContain("Enroll or resume");
  });

  it.each(["local", "openai_compatible"])(
    "requires fresh proof only for embedded downloads with %s",
    async (kind) => {
      const current = {
        ...profile,
        provider_kind: kind,
        base_url: kind === "local" ? "" : "http://localhost:11434/v1",
      };
      const actions: string[] = [];
      const checks: unknown[] = [];
      vi.stubGlobal(
        "$fetch",
        vi.fn(async (input: string, options?: { body?: { action?: string } }) => {
          if (input.endsWith("/operations/health")) return health;
          if (input.endsWith("/providers")) return [current];
          if (input.endsWith("/chats?limit=100"))
            return { items: [{ chat_id: -1001, status: "active" }] };
          if (input.includes("/query/catalog?"))
            return { profiles: [{ ...current, local_state: {} }] };
          if (input.endsWith("/auth/dev/step-up")) {
            actions.push(options?.body?.action ?? "");
            return {
              step_up: true,
              action: options?.body?.action,
              verified_at: new Date().toISOString(),
            };
          }
          if (input.endsWith("/operations/checks/local-model-readiness")) {
            checks.push(options?.body);
            return {
              check: "local-model-readiness",
              result: "ready",
              ok: true,
              details: {},
              warning: null,
              elapsed_ms: 1,
            };
          }
          throw new Error(`Unexpected request: ${input}`);
        }),
      );
      const wrapper = await mountSuspended(OperationsPage);
      await vi.waitFor(() => expect(wrapper.text()).toContain("local"));
      const checkbox = wrapper.find('input[type="checkbox"]');
      expect(checkbox.exists()).toBe(kind === "local");
      if (kind === "local") await checkbox.setValue(true);
      await wrapper
        .findAll("button")
        .find((button) => button.text() === "Run local check")!
        .trigger("click");
      await vi.waitFor(() => expect(checks).toHaveLength(1));
      expect(actions).toEqual(kind === "local" ? ["local_model:download"] : []);
      expect(checks[0]).toEqual({
        profile_name: "local",
        mode: null,
        allow_download: kind === "local",
      });
    },
  );
});
