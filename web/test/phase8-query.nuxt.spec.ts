import { clearNuxtState, useState } from "#app";
import { mountSuspended } from "@nuxt/test-utils/runtime";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import QueryPage from "../app/pages/query.vue";

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

const catalog = {
  scope: "all",
  chat_models: ["gpt-5.6-terra", "gpt-5.4-mini"],
  embedding_models: ["text-embedding-3-small", "all-MiniLM-L6-v2"],
  reasoning_levels: ["low", "medium", "high"],
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
      local_state: { dependency_available: true, model_loaded: true },
      pool: null,
    },
  ],
};

beforeEach(() => {
  clearNuxtState();
  useState("control-plane-session").value = session;
  useState("control-plane-session-status").value = "authenticated";
  useState("control-plane-session-bootstrapped").value = true;
  useState("control-plane-csrf").value = "csrf-token";
});

afterEach(() => vi.unstubAllGlobals());

describe("Phase 8 query workspace", () => {
  it("runs split-profile Search and Ask requests and renders bounded source references", async () => {
    const request = vi.fn(
      async (input: string, options?: { body?: Record<string, unknown>; method?: string }) => {
        if (input.endsWith("/query/catalog?scope=all")) return catalog;
        if (input.endsWith("/query/search")) {
          expect(options?.body).toMatchObject({
            query: "What did we decide?",
            scope: "all",
            retrieval_provider_profile: "local",
            chat_provider_profile: "primary",
            embedding_model: "all-MiniLM-L6-v2",
            chat_model: "gpt-5.6-terra",
            reasoning_level: "medium",
          });
          return {
            destination_scope: "all",
            source_scopes: ["-1001"],
            memory_profile: null,
            selection: {
              retrieval_provider_profile: "local",
              embedding_model: "all-MiniLM-L6-v2",
              chat_provider_profile: null,
              chat_model: null,
              reasoning_level: null,
            },
            results: [
              {
                content: "Use the scoped migration path.",
                score: 0.91,
                source: "summary",
                scope: "-1001",
                source_message_ids: [42, 43],
              },
            ],
            elapsed_ms: 18,
          };
        }
        if (input.endsWith("/query/ask")) {
          expect(options?.body).toMatchObject({
            question: "Why?",
            chat_provider_profile: "primary",
          });
          return {
            destination_scope: "all",
            source_scopes: ["-1001"],
            memory_profile: null,
            selection: {
              retrieval_provider_profile: "local",
              embedding_model: "all-MiniLM-L6-v2",
              chat_provider_profile: "primary",
              chat_model: "gpt-5.6-terra",
              reasoning_level: "medium",
            },
            results: [
              {
                content: "Use the scoped migration path.",
                score: 0.91,
                source: "summary",
                scope: "-1001",
                source_message_ids: [42, 43],
              },
            ],
            elapsed_ms: 44,
            answer: "Because it preserves topic isolation.",
            insufficient_context: false,
          };
        }
        throw new Error(`Unexpected request: ${input}`);
      },
    );
    vi.stubGlobal("$fetch", request);
    const wrapper = await mountSuspended(QueryPage);
    await vi.waitFor(() => expect(wrapper.text()).toContain("all-MiniLM-L6-v2"));

    await wrapper.get('[data-testid="retrieval-profile"]').setValue("local");
    await wrapper.get('[data-testid="query-input"]').setValue("What did we decide?");
    await wrapper.get('[data-testid="run-query"]').trigger("click");
    await vi.waitFor(() => expect(wrapper.text()).toContain("Use the scoped migration path."));
    expect(wrapper.text()).toContain("Messages 42, 43");
    expect(wrapper.text()).not.toContain("reasoning_content");

    await wrapper.get('[data-testid="query-mode"]').setValue("ask");
    await wrapper.get('[data-testid="query-input"]').setValue("Why?");
    await wrapper.get('[data-testid="run-query"]').trigger("click");
    await vi.waitFor(() =>
      expect(wrapper.text()).toContain("Because it preserves topic isolation."),
    );
  });

  it("cancels an in-flight query without presenting it as a backend failure", async () => {
    let requestSignal: AbortSignal | undefined;
    const request = vi.fn((input: string, options?: { signal?: AbortSignal }) => {
      if (input.endsWith("/query/catalog?scope=all")) return Promise.resolve(catalog);
      if (input.endsWith("/query/search")) {
        requestSignal = options?.signal;
        return new Promise((_resolve, reject) => {
          options?.signal?.addEventListener("abort", () =>
            reject(new DOMException("Aborted", "AbortError")),
          );
        });
      }
      throw new Error(`Unexpected request: ${input}`);
    });
    vi.stubGlobal("$fetch", request);
    const wrapper = await mountSuspended(QueryPage);
    await vi.waitFor(() => expect(wrapper.text()).toContain("all-MiniLM-L6-v2"));

    await wrapper.get('[data-testid="query-input"]').setValue("Cancel this query");
    await wrapper.get('[data-testid="run-query"]').trigger("click");
    await vi.waitFor(() =>
      expect(wrapper.find('[data-testid="cancel-query"]').exists()).toBe(true),
    );
    await wrapper.get('[data-testid="cancel-query"]').trigger("click");

    expect(requestSignal?.aborted).toBe(true);
    await vi.waitFor(() => expect(wrapper.text()).toContain("Request cancelled."));
    expect(wrapper.text()).not.toContain("Query failed");
  });
});
