import { clearNuxtState, useRouter, useState } from "#app";
import { mountSuspended } from "@nuxt/test-utils/runtime";
import { flushPromises } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import EmbeddingIndexesPage from "~/pages/embedding-indexes.vue";
import ControlPlaneShell from "~/components/ControlPlaneShell.vue";

const ownerSession = {
  user: { telegram_user_id: 111, first_name: "Owner" },
  client_kind: "browser",
  authenticated_at: "2026-11-07T12:00:00Z",
  step_up_at: null,
  expires_at: "2026-11-14T12:00:00Z",
  csrf_token: "csrf-token",
  capabilities: ["bot.admin"],
  chat_capabilities: {},
};
const profile = {
  name: "new-embedding",
  embedding_model: "prepared/embed",
  embedding_capable: true,
  chat_capable: false,
  enabled: true,
  local: true,
  provider_kind: "local",
};
const oldSpace = {
  id: 2,
  key: "old-space",
  provider_profile: "old-embedding",
  model: "old/embed",
  dimension: 384,
  created_at: "2026-11-01T12:00:00Z",
  eligible: 10,
  indexed: 10,
  missing: 0,
  stale: 0,
};
const queuedJob = {
  id: 7,
  scope: "all",
  provider_profile: "new-embedding",
  model: "prepared/embed",
  space_id: 3,
  status: "queued",
  version: 1,
  processed: 0,
  total: 10,
  error_code: null,
  created_at: "2026-11-07T12:00:00Z",
  updated_at: "2026-11-07T12:00:00Z",
};
const adoptionManifest = {
  scope: "all",
  provider_profile: "new-embedding",
  model: "prepared/embed",
  dimension: 384,
  original_config_hash: "c".repeat(64),
  entries: [{ embedding_id: 11, source_sha256: "a".repeat(64), vector_sha256: "b".repeat(64) }],
};
const adoptionPreview = {
  preview_hash: "d".repeat(64),
  count: 1,
  scope: "all",
  provider_profile: "new-embedding",
  model: "prepared/embed",
  dimension: 384,
  space_key: "e".repeat(64),
  configuration_fingerprint: "c".repeat(64),
  route_epoch: 3,
};

beforeEach(() => {
  clearNuxtState();
  useState("control-plane-session").value = ownerSession;
  useState("control-plane-session-status").value = "authenticated";
  useState("control-plane-session-bootstrapped").value = true;
  useState("control-plane-csrf").value = "csrf-token";
});
afterEach(() => vi.unstubAllGlobals());

function stubIndexes(initialJobs: (typeof queuedJob)[] = []) {
  let jobs = [...initialJobs];
  const request = vi.fn(
    async (input: string, options?: { method?: string; body?: Record<string, unknown> }) => {
      if (input.endsWith("/providers")) return [profile];
      if (input.endsWith("/embedding-indexes?scope=all"))
        return { scope: "all", active: oldSpace, spaces: [oldSpace], jobs };
      if (input.includes("/embedding-indexes/preview?"))
        return {
          scope: "all",
          provider_profile: "new-embedding",
          model: "prepared/embed",
          eligible: 10,
          indexed: 0,
          missing: 10,
          cost_warning: "Building this index may incur provider charges.",
          local_preparation_required: true,
        };
      if (input.endsWith("/embedding-indexes/jobs") && options?.method === "POST") {
        jobs = [queuedJob];
        return queuedJob;
      }
      if (input.endsWith("/embedding-indexes/jobs/7/run")) {
        jobs = [{ ...queuedJob, status: "ready", processed: 10, version: 2 }];
        return jobs[0];
      }
      if (input.endsWith("/embedding-indexes/jobs/7/activate")) {
        jobs = [{ ...queuedJob, status: "activated", processed: 10, version: 3 }];
        return jobs[0];
      }
      if (input.endsWith("/embedding-indexes/jobs/7/resume")) {
        jobs = [{ ...queuedJob, version: 5 }];
        return jobs[0];
      }
      if (input.endsWith("/embedding-indexes/jobs/7/cancel")) {
        jobs = [{ ...queuedJob, status: "cancelled", version: 6 }];
        return jobs[0];
      }
      if (input.endsWith("/embedding-indexes/legacy/preview")) return adoptionPreview;
      if (input.endsWith("/embedding-indexes/legacy/adopt"))
        return { adopted: 1, preview_hash: adoptionPreview.preview_hash, space: oldSpace };
      if (input.endsWith("/auth/dev/step-up"))
        return {
          step_up: true,
          action: options?.body?.action,
          verified_at: "2026-11-07T12:01:00Z",
        };
      throw new Error(`Unexpected request: ${input}`);
    },
  );
  vi.stubGlobal("$fetch", request);
  return request;
}

describe("Embedding index migrations", () => {
  it("requires reviewed provenance, exact preview confirmation and fresh verification for adoption", async () => {
    const request = stubIndexes();
    const wrapper = await mountSuspended(EmbeddingIndexesPage);
    await vi.waitFor(() => expect(wrapper.text()).toContain("old/embed"));
    await wrapper
      .get('[data-testid="legacy-adoption-manifest"]')
      .setValue(JSON.stringify(adoptionManifest));
    await wrapper.get('[data-testid="preview-legacy-adoption"]').trigger("click");
    await vi.waitFor(() => expect(wrapper.text()).toContain("1 legacy vector"));
    const adopt = wrapper.get('[data-testid="commit-legacy-adoption"]');
    expect(adopt.attributes("disabled")).toBeDefined();
    await wrapper
      .get('[data-testid="legacy-adoption-confirmation"]')
      .setValue(`ADOPT ${adoptionPreview.preview_hash}`);
    expect(adopt.attributes("disabled")).toBeDefined();
    await wrapper.get('[data-testid="legacy-provenance-attested"]').setValue(true);
    expect(request.mock.calls.some(([input]) => input.endsWith("/legacy/adopt"))).toBe(false);
    await adopt.trigger("click");
    await vi.waitFor(() => expect(wrapper.text()).toContain("Adopted 1 legacy vector"));
    expect(request).toHaveBeenCalledWith(
      "/api/control/v1/auth/dev/step-up",
      expect.objectContaining({ body: { action: "embedding:adopt" } }),
    );
    expect(request).toHaveBeenCalledWith(
      "/api/control/v1/embedding-indexes/legacy/adopt",
      expect.objectContaining({
        body: {
          manifest: adoptionManifest,
          preview_hash: adoptionPreview.preview_hash,
          confirmation: `ADOPT ${adoptionPreview.preview_hash}`,
          provenance_attested: true,
        },
      }),
    );
  });

  it("invalidates legacy adoption proof when its manifest changes", async () => {
    const request = stubIndexes();
    const wrapper = await mountSuspended(EmbeddingIndexesPage);
    await vi.waitFor(() => expect(wrapper.text()).toContain("old/embed"));
    const manifest = wrapper.get('[data-testid="legacy-adoption-manifest"]');
    await manifest.setValue(JSON.stringify(adoptionManifest));
    await wrapper.get('[data-testid="preview-legacy-adoption"]').trigger("click");
    await vi.waitFor(() =>
      expect(wrapper.find('[data-testid="commit-legacy-adoption"]').exists()).toBe(true),
    );
    await manifest.setValue(JSON.stringify({ ...adoptionManifest, scope: "-1001" }));
    expect(wrapper.find('[data-testid="commit-legacy-adoption"]').exists()).toBe(false);
    expect(request.mock.calls.some(([input]) => input.endsWith("/legacy/adopt"))).toBe(false);
  });
  it("keeps jobs visible when the backend canonicalizes a main-thread scope alias", async () => {
    const request = vi.fn(async (input: string, options?: { method?: string }) => {
      if (input.endsWith("/providers")) return [profile];
      if (input.includes("/embedding-indexes?scope="))
        return {
          scope: input.endsWith("all") ? "all" : "-1001",
          active: oldSpace,
          spaces: [oldSpace],
          jobs: [],
        };
      if (input.includes("/embedding-indexes/preview?"))
        return {
          scope: "-1001",
          provider_profile: "new-embedding",
          model: "prepared/embed",
          eligible: 10,
          indexed: 0,
          missing: 10,
          cost_warning: null,
          local_preparation_required: false,
        };
      if (input.endsWith("/embedding-indexes/jobs") && options?.method === "POST")
        return { ...queuedJob, scope: "-1001" };
      throw new Error(`Unexpected request: ${input}`);
    });
    vi.stubGlobal("$fetch", request);
    const wrapper = await mountSuspended(EmbeddingIndexesPage);
    await vi.waitFor(() => expect(wrapper.text()).toContain("old/embed"));
    await wrapper.get("#embedding-scope").setValue("-1001:main");
    await wrapper.get("form.inline-form").trigger("submit");
    await vi.waitFor(() => expect(wrapper.text()).toContain("old/embed"));
    await wrapper.get('[data-testid="preview-embedding-index"]').trigger("click");
    await vi.waitFor(() =>
      expect(wrapper.find('[data-testid="start-embedding-job"]').exists()).toBe(true),
    );
    await wrapper.get('[data-testid="start-embedding-job"]').trigger("click");
    await vi.waitFor(() =>
      expect(wrapper.find('[data-testid="run-embedding-job-7"]').exists()).toBe(true),
    );
  });
  it("loads metadata only and starts bounded work only after an explicit preview and start", async () => {
    const request = stubIndexes();
    const wrapper = await mountSuspended(EmbeddingIndexesPage);
    await vi.waitFor(() => expect(wrapper.text()).toContain("old/embed"));
    expect(request.mock.calls.every(([, options]) => options?.method === "GET")).toBe(true);
    expect(wrapper.find('[data-testid="start-embedding-job"]').exists()).toBe(false);
    await wrapper.get('[data-testid="preview-embedding-index"]').trigger("click");
    await vi.waitFor(() => expect(wrapper.text()).toContain("10 sources need indexing"));
    expect(wrapper.text()).toContain("Local model preparation is required");
    expect(wrapper.text()).toContain("may incur provider charges");
    await wrapper.get('[data-testid="start-embedding-job"]').trigger("click");
    await vi.waitFor(() => expect(wrapper.text()).toContain("queued"));
    expect(request).toHaveBeenCalledWith(
      "/api/control/v1/embedding-indexes/jobs",
      expect.objectContaining({
        method: "POST",
        body: { scope: "all", provider_profile: "new-embedding", model: "prepared/embed" },
      }),
    );
    expect(
      request.mock.calls.some(([input]) => input.endsWith("/run") || input.endsWith("/activate")),
    ).toBe(false);
    await wrapper.get('[data-testid="run-embedding-job-7"]').trigger("click");
    await vi.waitFor(() => expect(wrapper.text()).toContain("ready"));
    expect(request).toHaveBeenCalledWith(
      "/api/control/v1/embedding-indexes/jobs/7/run",
      expect.objectContaining({ body: { max_batches: 1 }, method: "POST" }),
    );
    expect(wrapper.text()).toContain("old/embed");
  });

  it("requires exact confirmation and fresh verification before versioned activation", async () => {
    const request = stubIndexes([{ ...queuedJob, status: "ready", processed: 10, version: 4 }]);
    const wrapper = await mountSuspended(EmbeddingIndexesPage);
    await vi.waitFor(() =>
      expect(wrapper.find('[data-testid="activate-embedding-job-7"]').exists()).toBe(true),
    );
    await wrapper.get('[data-testid="activate-embedding-job-7"]').trigger("click");
    expect(request.mock.calls.some(([input]) => input.endsWith("/activate"))).toBe(false);
    await wrapper.get('[data-testid="embedding-confirmation-7"]').setValue("ACTIVATE 7");
    await wrapper.get('[data-testid="activate-embedding-job-7"]').trigger("click");
    await vi.waitFor(() => expect(wrapper.text()).toContain("activated"));
    expect(request).toHaveBeenCalledWith(
      "/api/control/v1/auth/dev/step-up",
      expect.objectContaining({ body: { action: "embedding:activate" }, method: "POST" }),
    );
    expect(request).toHaveBeenCalledWith(
      "/api/control/v1/embedding-indexes/jobs/7/activate",
      expect.objectContaining({ body: { version: 4, confirmation: "ACTIVATE 7" }, method: "POST" }),
    );
  });

  it("resumes a failed job and cancels it with the current optimistic version", async () => {
    const request = stubIndexes([
      { ...queuedJob, status: "failed", error_code: "provider_unavailable", version: 4 },
    ]);
    const wrapper = await mountSuspended(EmbeddingIndexesPage);
    await vi.waitFor(() =>
      expect(wrapper.find('[data-testid="resume-embedding-job-7"]').exists()).toBe(true),
    );
    await wrapper.get('[data-testid="resume-embedding-job-7"]').trigger("click");
    await vi.waitFor(() =>
      expect(wrapper.find('[data-testid="cancel-embedding-job-7"]').exists()).toBe(true),
    );
    await wrapper.get('[data-testid="cancel-embedding-job-7"]').trigger("click");
    await vi.waitFor(() => expect(wrapper.text()).toContain("cancelled"));
    expect(request).toHaveBeenCalledWith(
      "/api/control/v1/embedding-indexes/jobs/7/resume",
      expect.objectContaining({ body: { version: 4 } }),
    );
    expect(request).toHaveBeenCalledWith(
      "/api/control/v1/embedding-indexes/jobs/7/cancel",
      expect.objectContaining({ body: { version: 5 } }),
    );
  });

  it("invalidates a preview when the target changes", async () => {
    stubIndexes();
    const wrapper = await mountSuspended(EmbeddingIndexesPage);
    await vi.waitFor(() => expect(wrapper.text()).toContain("old/embed"));
    await wrapper.get('[data-testid="preview-embedding-index"]').trigger("click");
    await vi.waitFor(() =>
      expect(wrapper.find('[data-testid="start-embedding-job"]').exists()).toBe(true),
    );
    await wrapper.get('[data-testid="embedding-target-model"]').setValue("different/model");
    expect(wrapper.find('[data-testid="start-embedding-job"]').exists()).toBe(false);
  });

  it("redirects scoped admins without showing owner navigation or requesting index data", async () => {
    useState("control-plane-session").value = {
      ...ownerSession,
      capabilities: [],
      chat_capabilities: { "-1001": ["chat.admin"] },
    };
    const request = vi.fn();
    vi.stubGlobal("$fetch", request);
    const shell = await mountSuspended(ControlPlaneShell);
    expect(shell.text()).not.toContain("Embedding indexes");
    const page = await mountSuspended(EmbeddingIndexesPage);
    await vi.waitFor(() => expect(useRouter().currentRoute.value.path).toBe("/unauthorized"));
    await flushPromises();
    expect(request).not.toHaveBeenCalled();
    page.unmount();
    shell.unmount();
  });
});
