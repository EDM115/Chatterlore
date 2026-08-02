import { clearNuxtState, useState } from "#app";
import { mountSuspended } from "@nuxt/test-utils/runtime";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import AuditPage from "../app/pages/audit.vue";
import OperationsPage from "../app/pages/operations.vue";
import StatsPage from "../app/pages/stats.vue";

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

const overview = {
  scope: "all",
  generated_at: "2026-08-02T12:00:00Z",
  memory: {
    messages: 180,
    learned: 150,
    skipped: 30,
    knowledge_items: 72,
    embeddings: 72,
    pending_reviews: 4,
    audit_events: 91,
  },
  ai_usage: { total: 44, errors: 2, average_latency_ms: 860 },
  storage: {
    database_bytes: 1048576,
    memory_bytes: 2048,
    memory_files: 3,
    memory_scan_truncated: false,
  },
  runtime: {
    uptime_seconds: 3661,
    schema_revision: "0016",
    scheduler: [
      {
        name: "pzp-idle-summarizer",
        state: "running",
        healthy: true,
        last_success_at: "2026-08-02T11:59:00Z",
        last_failure_at: null,
        last_exception_type: null,
        consecutive_failures: 0,
      },
    ],
  },
  administration: {
    bot_admins: 2,
    delegated_chat_admins: 3,
    managed_chats: { active: 4, paused: 1, detached: 1 },
  },
  providers: {
    profiles: 3,
    credentials: { eligible: 2, total: 3 },
    local_dependency_available: true,
    local_models_loaded: 1,
  },
  alerts: { states: { pending: 1, delivered: 8 } },
};

beforeEach(() => {
  clearNuxtState();
  useState("control-plane-session").value = session;
  useState("control-plane-session-status").value = "authenticated";
  useState("control-plane-session-bootstrapped").value = true;
  useState("control-plane-csrf").value = "csrf-token";
});

afterEach(() => vi.unstubAllGlobals());

describe("Phase 8 observability and operations", () => {
  it("loads scoped overview and bounded trend data with scheduler and hierarchy detail", async () => {
    const request = vi.fn(async (input: string) => {
      if (input.endsWith("/stats/overview?scope=all")) return overview;
      if (input.endsWith("/stats/timeseries?scope=all&days=30"))
        return {
          scope: "all",
          days: 30,
          points: [
            {
              date: "2026-08-01",
              messages: 20,
              learned: 17,
              skipped: 3,
              knowledge_items: 6,
              ai_calls: 4,
              ai_errors: 1,
            },
          ],
        };
      throw new Error(`Unexpected request: ${input}`);
    });
    vi.stubGlobal("$fetch", request);
    const wrapper = await mountSuspended(StatsPage);
    await vi.waitFor(() => expect(wrapper.text()).toContain("pzp-idle-summarizer"));

    expect(wrapper.text()).toContain("180");
    expect(wrapper.text()).toContain("2026-08-01");
    expect(wrapper.text()).toContain("4 active");
    expect(wrapper.text()).toContain("1 paused");
    expect(wrapper.text()).toContain("Schema 0016");
  });

  it("presents bounded safe audit and AI-usage records with scoped filters", async () => {
    const request = vi.fn(async (input: string) => {
      if (input.endsWith("/audit/events?scope=all&limit=25"))
        return {
          items: [
            {
              id: 9,
              event_type: "credential_disabled",
              scope: null,
              user_id: 111,
              payload: { profile: "primary", fingerprint: "ab12", safe: "visible" },
              created_at: "2026-08-02T11:00:00Z",
            },
          ],
          next_cursor: null,
          has_more: false,
        };
      if (input.endsWith("/audit/ai-usage?scope=all&limit=25"))
        return {
          items: [
            {
              id: 5,
              scope: "-1001",
              provider: "openai_compatible",
              model: "gpt-5.6-terra",
              operation: "answer",
              prompt_tokens: 120,
              completion_tokens: 30,
              latency_ms: 900,
              error: null,
              created_at: "2026-08-02T10:00:00Z",
            },
          ],
          next_cursor: null,
          has_more: false,
        };
      throw new Error(`Unexpected request: ${input}`);
    });
    vi.stubGlobal("$fetch", request);
    const wrapper = await mountSuspended(AuditPage);
    await vi.waitFor(() => expect(wrapper.text()).toContain("credential_disabled"));

    expect(wrapper.text()).toContain("gpt-5.6-terra");
    expect(wrapper.text()).toContain("120 prompt");
    expect(wrapper.text()).toContain('"safe": "visible"');
    expect(wrapper.text()).not.toContain("api_key");
  });

  it("keeps cached health side-effect free and labels explicit paid/download checks", async () => {
    const request = vi.fn(async (input: string, options?: { body?: unknown; method?: string }) => {
      if (input.endsWith("/operations/health"))
        return {
          generated_at: "2026-08-02T12:00:00Z",
          checks: [
            "db-integrity",
            "local-model-readiness",
            "projection-status",
            "provider-profile",
          ],
          provider_calls_performed: false,
          runtime: overview.runtime,
          storage: overview.storage,
          providers: overview.providers,
          alerts: overview.alerts,
        };
      if (input.endsWith("/query/catalog?scope=all"))
        return {
          scope: "all",
          chat_models: ["gpt-5.6-terra"],
          embedding_models: ["text-embedding-3-small", "all-MiniLM-L6-v2"],
          reasoning_levels: ["medium"],
          profiles: [
            {
              name: "primary",
              provider_kind: "openai_compatible",
              source: "managed",
              enabled: true,
              chat_model: "gpt-5.6-terra",
              embedding_model: "text-embedding-3-small",
              capabilities: ["chat", "embedding", "reasoning"],
              availability: "ready",
              local_state: null,
              pool: { eligible: 2, total: 2 },
            },
            {
              name: "local",
              provider_kind: "local",
              source: "environment",
              enabled: true,
              chat_model: "none",
              embedding_model: "all-MiniLM-L6-v2",
              capabilities: ["embedding"],
              availability: "ready",
              local_state: { dependency_available: true, model_loaded: false },
              pool: null,
            },
          ],
        };
      if (input.endsWith("/operations/checks/db-integrity") && options?.method === "POST")
        return {
          check: "db-integrity",
          ok: true,
          result: "ok",
          details: {},
          warning: null,
          elapsed_ms: 2,
        };
      if (input.endsWith("/operations/checks/provider-profile") && options?.method === "POST") {
        expect(options.body).toEqual({
          profile_name: "primary",
          mode: "chat",
          allow_download: false,
        });
        return {
          check: "provider-profile",
          ok: true,
          result: "ready",
          details: { profile_name: "primary", model: "gpt-5.6-terra", latency_ms: 220 },
          warning: "This explicit manual check may incur provider usage charges.",
          elapsed_ms: 221,
        };
      }
      throw new Error(`Unexpected request: ${input}`);
    });
    vi.stubGlobal("$fetch", request);
    const wrapper = await mountSuspended(OperationsPage);
    await vi.waitFor(() => expect(wrapper.text()).toContain("No provider call performed"));

    expect(wrapper.text()).toContain("1 pending");
    expect(wrapper.text()).toContain("may incur provider usage charges");
    expect(wrapper.text()).toContain("may download model files");
    await wrapper.get('[data-testid="check-db-integrity"]').trigger("click");
    await vi.waitFor(() => expect(wrapper.text()).toContain("db-integrity · ok"));
    await wrapper.get('[data-testid="check-provider-profile"]').trigger("click");
    await vi.waitFor(() => expect(wrapper.text()).toContain("gpt-5.6-terra"));
  });
});
