import { clearNuxtState, useState } from "#app";
import { mountSuspended } from "@nuxt/test-utils/runtime";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ControlPlaneShell from "~/components/ControlPlaneShell.vue";
import DashboardPage from "~/pages/index.vue";

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

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("Phase 8 dashboard and navigation", () => {
  it("offers every authorized product workspace in desktop and mobile navigation", async () => {
    const wrapper = await mountSuspended(ControlPlaneShell);
    const expected = [
      "Dashboard",
      "Chats",
      "Query",
      "Memory",
      "Stats",
      "Audit",
      "Operations",
      "Bot Settings",
      "Providers",
    ];

    for (const label of expected) {
      expect(wrapper.get('nav[aria-label="Primary navigation"]').text()).toContain(label);
      expect(wrapper.get('nav[aria-label="Mobile navigation"]').text()).toContain(label);
    }
  });

  it("loads one safe overview, trend and cached health summary without a provider call", async () => {
    const request = vi.fn(async (input: string) => {
      if (input.endsWith("/stats/overview?scope=all")) {
        return {
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
          runtime: { uptime_seconds: 3661, schema_revision: "0017", scheduler: [] },
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
      }
      if (input.endsWith("/stats/timeseries?scope=all&days=14")) {
        return {
          scope: "all",
          days: 14,
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
      }
      if (input.endsWith("/operations/health")) {
        return {
          generated_at: "2026-08-02T12:00:00Z",
          checks: ["db-integrity", "provider-profile"],
          provider_calls_performed: false,
          runtime: { uptime_seconds: 3661, schema_revision: "0017", scheduler: [] },
          storage: { database_bytes: 1048576, memory_bytes: 2048 },
          providers: { profiles: 3, credentials: { eligible: 2, total: 3 } },
          alerts: { states: { pending: 1, delivered: 8 } },
        };
      }
      throw new Error(`Unexpected request: ${input}`);
    });
    vi.stubGlobal("$fetch", request);

    const wrapper = await mountSuspended(DashboardPage);
    await vi.waitFor(() => expect(wrapper.text()).toContain("180"));

    expect(wrapper.get('[data-testid="dashboard-health"]').text()).toContain("Cached status");
    expect(wrapper.get('[data-testid="dashboard-health"]').text()).toContain("No provider call");
    expect(wrapper.text()).toContain("72");
    expect(wrapper.text()).toContain("4 active");
    expect(wrapper.text()).toContain("1 pending");
    expect(wrapper.text()).toContain("2026-08-01");
    expect(request).toHaveBeenCalledTimes(3);
  });
});
