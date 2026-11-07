import { clearNuxtState, useState } from "#app";
import { mountSuspended } from "@nuxt/test-utils/runtime";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import MemoryPage from "~/pages/memory.vue";

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

describe("Phase 8 Memory Explorer", () => {
  it("pages items and reviews, gates raw source access, mutates records and exposes streamed exports", async () => {
    let itemDeleted = false;
    const request = vi.fn(async (input: string, options?: { body?: unknown; method?: string }) => {
      if (input.endsWith("/memory/items?scope=all&limit=25&cursor=0")) {
        return {
          items: itemDeleted
            ? []
            : [
                {
                  id: 7,
                  scope: "-1001",
                  kind: "summary",
                  content: "Deploy behind one Caddy origin.",
                  source_message_ids: [41],
                  metadata: { topic: "deployment" },
                  created_at: "2026-08-01T10:00:00Z",
                  updated_at: "2026-08-01T11:00:00Z",
                },
              ],
          next_cursor: null,
          has_more: false,
        };
      }
      if (input.endsWith("/memory/reviews?scope=all&limit=25&cursor=0")) {
        return {
          items: [
            {
              id: 12,
              scope: "-1001:22",
              kind: "summary",
              content: "Review this compacted decision.",
              source_message_ids: [51],
              created_at: "2026-08-01T12:00:00Z",
            },
          ],
          next_cursor: null,
          has_more: false,
        };
      }
      if (input.endsWith("/memory/items/7/sources") && options?.method === "POST") {
        return {
          item_id: 7,
          scope: "-1001",
          sources: [
            {
              telegram_message_id: 41,
              user_id: 222,
              username: "alice",
              text: "Sensitive source shown only after the explicit action.",
              created_at: "2026-08-01T09:58:00Z",
              message_permalink: "https://t.me/c/1001/41",
            },
          ],
        };
      }
      if (input.endsWith("/memory/items/7/refresh") && options?.method === "POST") {
        expect(options.body).toEqual({ mode: "both" });
        return {
          id: 7,
          scope: "-1001",
          kind: "summary",
          mode: "both",
          summarized: true,
          embeddings_refreshed: 1,
        };
      }
      if (input.endsWith("/memory/reviews/12/approve") && options?.method === "POST") {
        return { review_id: 12, status: "approved", knowledge_item_id: 8 };
      }
      if (input.endsWith("/memory/items/7") && options?.method === "DELETE") {
        itemDeleted = true;
        return { audit_event_id: 90, version: 1 };
      }
      throw new Error(`Unexpected request: ${input}`);
    });
    vi.stubGlobal("$fetch", request);
    const wrapper = await mountSuspended(MemoryPage);
    await vi.waitFor(() => expect(wrapper.text()).toContain("Deploy behind one Caddy origin."));

    expect(wrapper.get('[data-testid="export-md"]').attributes("href")).toBe(
      "/api/control/v1/memory/export?scope=all&format=md",
    );
    expect(wrapper.get('[data-testid="export-jsonl"]').attributes("href")).toBe(
      "/api/control/v1/memory/export?scope=all&format=jsonl",
    );
    expect(wrapper.text()).not.toContain("Sensitive source shown only after the explicit action.");

    await wrapper.get('[data-testid="raw-sources-7"]').trigger("click");
    await vi.waitFor(() =>
      expect(wrapper.text()).toContain("Sensitive source shown only after the explicit action."),
    );
    expect(wrapper.text()).toContain("@alice");

    await wrapper.get('[data-testid="refresh-mode-7"]').setValue("both");
    await wrapper.get('[data-testid="refresh-item-7"]').trigger("click");
    await vi.waitFor(() => expect(wrapper.text()).toContain("1 embedding"));

    await wrapper.get('[data-testid="approve-review-12"]').trigger("click");
    await vi.waitFor(() => expect(wrapper.text()).not.toContain("Review this compacted decision."));

    await wrapper.get('[data-testid="delete-item-7"]').trigger("click");
    expect(wrapper.text()).toContain("Delete item 7 permanently?");
    await wrapper.get('[data-testid="confirm-delete-item-7"]').trigger("click");
    await vi.waitFor(() => expect(wrapper.text()).not.toContain("Deploy behind one Caddy origin."));
  });
});
