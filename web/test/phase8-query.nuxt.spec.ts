import { clearNuxtState, useState } from "#app";
import { mountSuspended } from "@nuxt/test-utils/runtime";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import QueryPage from "~/pages/query.vue";

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
  scope: "-1001",
  chat_models: ["gpt-6.1-terra", "gpt-5.4-mini"],
  embedding_models: ["text-embedding-3-small", "all-MiniLM-L6-v2"],
  reasoning_levels: ["low", "medium", "high"],
  profiles: [
    {
      name: "primary",
      provider_kind: "openai_compatible",
      source: "managed",
      enabled: true,
      chat_model: "gpt-6.1-terra",
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
      local_state: {
        dependency_available: true,
        model_loaded: true,
        chat_dependency_available: false,
        embedding_dependency_available: true,
        chat_model_loaded: false,
        embedding_model_loaded: true,
        server_configured: false,
      },
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
  it("uses configured no-auth local HTTP providers for Search and Ask without claiming reachability", async () => {
    const configured = {
      name: "local-server",
      provider_kind: "openai_compatible",
      source: "managed",
      enabled: true,
      chat_model: "server-chat",
      embedding_model: "server-embed",
      local: true,
      api_dialect: "generic",
      auth_mode: "none",
      capabilities: ["chat", "embedding"],
      availability: "configured",
      local_state: {
        dependency_available: true,
        model_loaded: false,
        chat_dependency_available: false,
        embedding_dependency_available: false,
        chat_model_loaded: false,
        embedding_model_loaded: false,
        server_configured: true,
      },
      pool: null,
    };
    const requests: Record<string, unknown>[] = [];
    vi.stubGlobal(
      "$fetch",
      vi.fn(async (input: string, options?: { body?: Record<string, unknown> }) => {
        if (input.endsWith("/chats?limit=100"))
          return {
            items: [{ chat_id: -1001, status: "active" }],
            next_cursor: null,
            has_more: false,
          };
        if (input.includes("/query/catalog?"))
          return {
            ...catalog,
            chat_models: ["server-chat"],
            embedding_models: ["server-embed"],
            profiles: [
              configured,
              {
                ...configured,
                name: "unconfigured",
                availability: "unavailable",
                local_state: { ...configured.local_state, server_configured: false },
              },
              { ...configured, name: "disabled", enabled: false, availability: "disabled" },
            ],
          };
        if (input.endsWith("/query/search") || input.endsWith("/query/ask")) {
          requests.push(options?.body ?? {});
          return {
            destination_scope: "-1001",
            source_scopes: [],
            memory_profile: null,
            selection: options?.body,
            results: [],
            elapsed_ms: 1,
            ...(input.endsWith("/query/ask")
              ? { answer: "Server answer", insufficient_context: false }
              : {}),
          };
        }
        throw new Error(`Unexpected request: ${input}`);
      }),
    );
    const wrapper = await mountSuspended(QueryPage);
    await vi.waitFor(() =>
      expect(wrapper.find('[data-testid="retrieval-profile"]').exists()).toBe(true),
    );
    expect(wrapper.get('[data-testid="retrieval-profile"]').text()).toBe(
      "local-server · configured",
    );
    expect(wrapper.get('[data-testid="chat-profile"]').text()).toBe("local-server · configured");
    expect(wrapper.text()).not.toContain("local-server · ready");
    await wrapper.get('[data-testid="query-input"]').setValue("What changed?");
    await wrapper.get('[data-testid="run-query"]').trigger("click");
    await vi.waitFor(() => expect(requests).toHaveLength(1));
    expect(requests[0]).toMatchObject({
      retrieval_provider_profile: "local-server",
      embedding_model: "server-embed",
      chat_provider_profile: null,
    });
    await wrapper.get('[data-testid="query-mode"]').setValue("ask");
    await wrapper.get('[data-testid="run-query"]').trigger("click");
    await vi.waitFor(() => expect(wrapper.text()).toContain("Server answer"));
    expect(requests[1]).toMatchObject({
      retrieval_provider_profile: "local-server",
      chat_provider_profile: "local-server",
      chat_model: "server-chat",
      reasoning_level: null,
    });
  });

  it.each([
    {
      readyLeg: "embedding",
      availability: "preparation_required",
      chatDependency: true,
      embeddingDependency: true,
      otherSelectable: true,
    },
    {
      readyLeg: "embedding",
      availability: "unavailable",
      chatDependency: false,
      embeddingDependency: true,
      otherSelectable: false,
    },
    {
      readyLeg: "chat",
      availability: "preparation_required",
      chatDependency: true,
      embeddingDependency: true,
      otherSelectable: true,
    },
    {
      readyLeg: "chat",
      availability: "unavailable",
      chatDependency: true,
      embeddingDependency: false,
      otherSelectable: false,
    },
  ])(
    "uses the resident embedded $readyLeg leg without an aggregate $availability gate",
    async ({ readyLeg, availability, chatDependency, embeddingDependency, otherSelectable }) => {
      const partial = {
        ...catalog.profiles[1],
        name: "partial-local",
        chat_model: "local-chat",
        embedding_model: "local-embed",
        capabilities: ["chat", "embedding"],
        availability,
        local_state: {
          dependency_available: chatDependency && embeddingDependency,
          model_loaded: false,
          chat_dependency_available: chatDependency,
          embedding_dependency_available: embeddingDependency,
          chat_model_loaded: readyLeg === "chat",
          embedding_model_loaded: readyLeg === "embedding",
          server_configured: false,
        },
      };
      let body: Record<string, unknown> | undefined;
      vi.stubGlobal(
        "$fetch",
        vi.fn(async (input: string, options?: { body?: Record<string, unknown> }) => {
          if (input.endsWith("/chats?limit=100"))
            return {
              items: [{ chat_id: -1001, status: "active" }],
              next_cursor: null,
              has_more: false,
            };
          if (input.includes("/query/catalog?"))
            return {
              ...catalog,
              chat_models: ["local-chat", "gpt-6.1-terra"],
              embedding_models: ["local-embed", "text-embedding-3-small"],
              profiles: [
                partial,
                {
                  ...catalog.profiles[0],
                  capabilities: [readyLeg === "embedding" ? "chat" : "embedding"],
                },
              ],
            };
          if (input.endsWith("/query/search") || input.endsWith("/query/ask")) {
            body = options?.body;
            return {
              destination_scope: "-1001",
              source_scopes: [],
              memory_profile: null,
              selection: body,
              results: [],
              elapsed_ms: 1,
              ...(input.endsWith("/query/ask")
                ? { answer: "Partial profile answer", insufficient_context: false }
                : {}),
            };
          }
          throw new Error(`Unexpected request: ${input}`);
        }),
      );
      const wrapper = await mountSuspended(QueryPage);
      await vi.waitFor(() =>
        expect(wrapper.find('[data-testid="retrieval-profile"]').exists()).toBe(true),
      );
      const readySelector = readyLeg === "embedding" ? "retrieval-profile" : "chat-profile";
      const otherSelector = readyLeg === "embedding" ? "chat-profile" : "retrieval-profile";
      expect(wrapper.get(`[data-testid="${readySelector}"]`).text()).toContain("partial-local");
      expect(wrapper.get(`[data-testid="${otherSelector}"]`).text().includes("partial-local")).toBe(
        otherSelectable,
      );
      if (readyLeg === "chat") {
        await wrapper.get('[data-testid="query-mode"]').setValue("ask");
        await wrapper.get('[data-testid="retrieval-profile"]').setValue("primary");
      }
      await wrapper.get('[data-testid="query-input"]').setValue("Independent provider modes");
      await wrapper.get('[data-testid="run-query"]').trigger("click");
      await vi.waitFor(() => expect(body).toBeDefined());
      expect(body).toMatchObject(
        readyLeg === "embedding"
          ? {
              retrieval_provider_profile: "partial-local",
              embedding_model: "local-embed",
              chat_provider_profile: null,
            }
          : {
              retrieval_provider_profile: "primary",
              chat_provider_profile: "partial-local",
              chat_model: "local-chat",
            },
      );
    },
  );

  it("allows unloaded local models to reach the backend after restart or cache eviction", async () => {
    let body: Record<string, unknown> | undefined;
    vi.stubGlobal(
      "$fetch",
      vi.fn(async (input: string, options?: { body?: Record<string, unknown> }) => {
        if (input.endsWith("/chats?limit=100"))
          return {
            items: [{ chat_id: -1001, status: "active" }],
            next_cursor: null,
            has_more: false,
          };
        if (input.includes("/query/catalog?"))
          return {
            ...catalog,
            chat_models: ["local-chat"],
            embedding_models: ["local-embed"],
            profiles: [
              {
                ...catalog.profiles[1],
                name: "unloaded-local",
                chat_model: "local-chat",
                embedding_model: "local-embed",
                capabilities: ["chat", "embedding"],
                availability: "preparation_required",
                local_state: {
                  dependency_available: true,
                  model_loaded: false,
                  chat_dependency_available: true,
                  embedding_dependency_available: true,
                  chat_model_loaded: false,
                  embedding_model_loaded: false,
                  server_configured: false,
                },
              },
            ],
          };
        if (input.endsWith("/query/ask")) {
          body = options?.body;
          return {
            destination_scope: "-1001",
            source_scopes: [],
            memory_profile: null,
            selection: body,
            results: [],
            elapsed_ms: 1,
            answer: "Loaded on demand",
            insufficient_context: false,
          };
        }
        throw new Error(`Unexpected request: ${input}`);
      }),
    );
    const wrapper = await mountSuspended(QueryPage);
    await vi.waitFor(() =>
      expect(wrapper.find('[data-testid="retrieval-profile"]').exists()).toBe(true),
    );
    expect(wrapper.get('[data-testid="retrieval-profile"]').text()).toContain("unloaded-local");
    expect(wrapper.get('[data-testid="chat-profile"]').text()).toContain("unloaded-local");
    expect(wrapper.text()).toContain("model not loaded");
    await wrapper.get('[data-testid="query-mode"]').setValue("ask");
    await wrapper.get('[data-testid="query-input"]').setValue("Load only the requested models");
    await wrapper.get('[data-testid="run-query"]').trigger("click");
    await vi.waitFor(() => expect(wrapper.text()).toContain("Loaded on demand"));
    expect(body).toMatchObject({
      retrieval_provider_profile: "unloaded-local",
      chat_provider_profile: "unloaded-local",
      embedding_model: "local-embed",
      chat_model: "local-chat",
    });
  });

  it("searches with an embedding-only profile without sending invalid empty chat selections", async () => {
    let searchBody: Record<string, unknown> | undefined;
    vi.stubGlobal(
      "$fetch",
      vi.fn(async (input: string, options?: { body?: Record<string, unknown> }) => {
        if (input.endsWith("/chats?limit=100"))
          return {
            items: [{ chat_id: -1001, status: "active" }],
            next_cursor: null,
            has_more: false,
          };
        if (input.includes("/query/catalog?"))
          return { ...catalog, chat_models: [], profiles: [catalog.profiles[1]] };
        if (input.endsWith("/query/search")) {
          searchBody = options?.body;
          return {
            destination_scope: "-1001",
            source_scopes: ["-1001"],
            memory_profile: null,
            selection: {
              retrieval_provider_profile: "local",
              embedding_model: "all-MiniLM-L6-v2",
              chat_provider_profile: null,
              chat_model: null,
              reasoning_level: null,
            },
            results: [],
            elapsed_ms: 1,
          };
        }
        throw new Error(`Unexpected request: ${input}`);
      }),
    );
    const wrapper = await mountSuspended(QueryPage);
    await vi.waitFor(() => expect(wrapper.text()).toContain("all-MiniLM-L6-v2"));
    await wrapper.get('[data-testid="query-input"]').setValue("project decision");
    await wrapper.get('[data-testid="run-query"]').trigger("click");
    await vi.waitFor(() => expect(searchBody).toBeDefined());
    expect(searchBody).toMatchObject({
      query: "project decision",
      retrieval_provider_profile: "local",
      chat_provider_profile: null,
      chat_model: null,
      reasoning_level: null,
    });
  });

  it("runs split-profile Search and Ask requests and renders bounded source references", async () => {
    const request = vi.fn(
      async (input: string, options?: { body?: Record<string, unknown>; method?: string }) => {
        if (input.endsWith("/chats?limit=100"))
          return {
            items: [
              {
                chat_id: -1001,
                title: "Active group",
                username: null,
                status: "active",
                version: 1,
                updated_at: "2026-08-02T10:00:00Z",
              },
            ],
            next_cursor: null,
            has_more: false,
          };
        if (input.endsWith("/query/catalog?scope=-1001")) return catalog;
        if (input.endsWith("/query/search")) {
          expect(options?.body).toMatchObject({
            query: "What did we decide?",
            scope: "-1001",
            retrieval_provider_profile: "local",
            chat_provider_profile: null,
            embedding_model: "all-MiniLM-L6-v2",
            chat_model: null,
            reasoning_level: null,
          });
          return {
            destination_scope: "-1001",
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
              chat_model: "gpt-6.1-terra",
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
      if (input.endsWith("/chats?limit=100"))
        return Promise.resolve({
          items: [
            {
              chat_id: -1001,
              title: "Active group",
              username: null,
              status: "active",
              version: 1,
              updated_at: "2026-08-02T10:00:00Z",
            },
          ],
          next_cursor: null,
          has_more: false,
        });
      if (input.endsWith("/query/catalog?scope=-1001")) return Promise.resolve(catalog);
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
