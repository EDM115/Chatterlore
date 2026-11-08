<script setup lang="ts">
import type { components } from "~/types/openapi";

definePageMeta({ middleware: "auth" });
useSeoMeta({ title: "Embedding indexes · Chatterlore Control Plane" });

interface SpaceView {
  id: number;
  key: string;
  provider_profile: string;
  model: string;
  dimension: number;
  created_at: string;
}
interface SpaceCoverage extends SpaceView {
  eligible: number;
  indexed: number;
  missing: number;
  stale: number;
}
interface IndexJob {
  id: number;
  scope: string;
  provider_profile: string;
  model: string;
  space_id: number | null;
  status: "queued" | "running" | "ready" | "failed" | "cancelled" | "activated";
  version: number;
  processed: number;
  total: number;
  error_code: string | null;
  created_at: string;
  updated_at: string;
}
interface IndexOverview {
  scope: string;
  active: SpaceView | null;
  spaces: SpaceCoverage[];
  jobs: IndexJob[];
}
interface IndexPreview {
  scope: string;
  provider_profile: string;
  model: string;
  eligible: number;
  indexed: number;
  missing: number;
  cost_warning: string | null;
  local_preparation_required: boolean;
}

type ProviderProfile = components["schemas"]["ProviderProfileResponse"];
type LegacyManifest = components["schemas"]["LegacyAdoptionManifest"];
type LegacyPreview = components["schemas"]["LegacyAdoptionPreviewView"];
const api = useControlApi();
const controlSession = useControlSession();
const isOwner = computed(() => controlSession.hasCapability("bot.admin"));
const scope = ref("all");
const selectedProfile = ref("");
const model = ref("");
const profiles = ref<ProviderProfile[]>([]);
const overview = ref<IndexOverview | null>(null);
const preview = ref<IndexPreview | null>(null);
const confirmations = ref<Record<number, string>>({});
const activeOperation = ref<string | null>(null);
const error = ref<string | null>(null);
const notice = ref<string | null>(null);
const adoptionManifestText = ref("");
const adoptionManifest = ref<LegacyManifest | null>(null);
const adoptionPreview = ref<LegacyPreview | null>(null);
const adoptionPreviewSource = ref("");
const adoptionConfirmation = ref("");
const provenanceAttested = ref(false);
const canAdopt = computed(
  () =>
    adoptionPreview.value !== null &&
    adoptionPreviewSource.value === adoptionManifestText.value &&
    provenanceAttested.value &&
    adoptionConfirmation.value === `ADOPT ${adoptionPreview.value.preview_hash}`,
);
const embeddingProfiles = computed(() =>
  profiles.value.filter((profile) => profile.enabled && profile.embedding_capable),
);
const targetKey = computed(() =>
  JSON.stringify([scope.value.trim(), selectedProfile.value, model.value.trim()]),
);
const previewTargetKey = ref("");
const canStart = computed(
  () => preview.value !== null && targetKey.value === previewTargetKey.value,
);
let overviewRequest = 0;

onMounted(async () => {
  if (!isOwner.value) {
    await navigateTo("/unauthorized");
    return;
  }
  activeOperation.value = "load";
  try {
    profiles.value = await api.request<ProviderProfile[]>("/providers");
    selectedProfile.value = embeddingProfiles.value[0]?.name ?? "";
    model.value = embeddingProfiles.value[0]?.embedding_model ?? "";
    await refreshOverview();
  } catch (caught) {
    error.value = controlErrorMessage(caught, "Embedding indexes could not be loaded.");
  } finally {
    activeOperation.value = null;
  }
});
watch(selectedProfile, () => {
  model.value =
    embeddingProfiles.value.find((profile) => profile.name === selectedProfile.value)
      ?.embedding_model ?? "";
});
watch(targetKey, () => {
  preview.value = null;
  previewTargetKey.value = "";
});
watch(scope, () => {
  overviewRequest += 1;
  overview.value = null;
  confirmations.value = {};
});
watch(adoptionManifestText, () => {
  adoptionPreview.value = null;
  adoptionManifest.value = null;
  adoptionPreviewSource.value = "";
  adoptionConfirmation.value = "";
  provenanceAttested.value = false;
});

async function previewLegacyAdoption() {
  if (!isOwner.value || activeOperation.value !== null) return;
  const source = adoptionManifestText.value;
  let manifest: LegacyManifest;
  try {
    if (new TextEncoder().encode(source).byteLength > 65_536) throw new Error();
    const parsed: unknown = JSON.parse(source);
    if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) throw new Error();
    manifest = parsed as LegacyManifest;
  } catch {
    error.value = "Paste a valid JSON provenance manifest (maximum 64 KiB, at most 100 mappings).";
    return;
  }
  activeOperation.value = "legacy-preview";
  error.value = notice.value = null;
  adoptionPreview.value = null;
  provenanceAttested.value = false;
  adoptionConfirmation.value = "";
  try {
    const result = await api.request<LegacyPreview>("/embedding-indexes/legacy/preview", {
      method: "POST",
      body: { manifest },
    });
    if (source === adoptionManifestText.value) {
      adoptionPreview.value = result;
      adoptionManifest.value = manifest;
      adoptionPreviewSource.value = source;
    }
  } catch (caught) {
    error.value = indexError(
      caught,
      "Original provenance could not be validated. Reindex if original records are unavailable.",
    );
  } finally {
    activeOperation.value = null;
  }
}

async function commitLegacyAdoption() {
  if (
    !isOwner.value ||
    !canAdopt.value ||
    !adoptionPreview.value ||
    !adoptionManifest.value ||
    activeOperation.value !== null
  )
    return;
  const preview = adoptionPreview.value;
  const manifest = adoptionManifest.value;
  activeOperation.value = "legacy-adopt";
  error.value = notice.value = null;
  try {
    if (!(await controlSession.ensureStepUp("embedding:adopt", "/embedding-indexes"))) {
      error.value = "Fresh Telegram verification is required before adopting legacy vectors.";
      return;
    }
    if (!isOwner.value || !canAdopt.value || adoptionPreview.value !== preview) return;
    const result = await api.request<components["schemas"]["LegacyAdoptionCommitView"]>(
      "/embedding-indexes/legacy/adopt",
      {
        method: "POST",
        body: {
          manifest,
          preview_hash: preview.preview_hash,
          confirmation: adoptionConfirmation.value,
          provenance_attested: true,
        },
      },
    );
    adoptionManifestText.value = "";
    notice.value = `Adopted ${result.adopted} legacy vector${result.adopted === 1 ? "" : "s"}. Vector bytes and route settings were preserved.`;
    await refreshOverview();
  } catch (caught) {
    error.value = indexError(
      caught,
      "Legacy adoption failed safely. Refresh the original manifest and preview before retrying.",
    );
  } finally {
    activeOperation.value = null;
  }
}

async function refreshOverview() {
  if (!isOwner.value || !scope.value.trim()) return;
  const request = ++overviewRequest;
  const currentScope = scope.value.trim();
  const result = await api.request<IndexOverview>(
    `/embedding-indexes?scope=${encodeURIComponent(currentScope)}`,
  );
  if (request === overviewRequest && scope.value.trim() === currentScope) overview.value = result;
}

async function loadScope() {
  if (activeOperation.value !== null) return;
  activeOperation.value = "load";
  error.value = null;
  try {
    await refreshOverview();
  } catch (caught) {
    error.value = controlErrorMessage(caught, "Index coverage could not be refreshed.");
  } finally {
    activeOperation.value = null;
  }
}

async function previewIndex() {
  if (!isOwner.value || activeOperation.value !== null) return;
  if (!scope.value.trim() || !selectedProfile.value) {
    error.value = "Enter a scope and choose an enabled embedding-capable provider.";
    return;
  }
  const target = targetKey.value;
  const params = new URLSearchParams({
    scope: scope.value.trim(),
    provider_profile: selectedProfile.value,
  });
  if (model.value.trim()) params.set("model", model.value.trim());
  activeOperation.value = "preview";
  preview.value = null;
  error.value = null;
  notice.value = null;
  try {
    const result = await api.request<IndexPreview>(`/embedding-indexes/preview?${params}`);
    if (target === targetKey.value) {
      preview.value = result;
      previewTargetKey.value = target;
    }
  } catch (caught) {
    error.value = controlErrorMessage(
      caught,
      "The replacement index could not be previewed. Check the scope and provider model.",
    );
  } finally {
    activeOperation.value = null;
  }
}

async function startJob() {
  if (!isOwner.value || !canStart.value || !preview.value || activeOperation.value !== null) return;
  activeOperation.value = "start";
  error.value = null;
  try {
    const job = await api.request<IndexJob>("/embedding-indexes/jobs", {
      method: "POST",
      body: {
        scope: preview.value.scope,
        provider_profile: preview.value.provider_profile,
        model: preview.value.model,
      },
    });
    upsertJob(job);
    preview.value = null;
    notice.value = `Job ${job.id} queued. Run one bounded batch at a time. The active index has not changed.`;
  } catch (caught) {
    error.value = indexError(caught, "The migration job could not be created.");
  } finally {
    activeOperation.value = null;
  }
}

async function operateJob(
  job: IndexJob,
  action: "run" | "resume" | "cancel" | "activate" | "refresh",
) {
  if (!isOwner.value || activeOperation.value !== null) return;
  const expected = `ACTIVATE ${job.id}`;
  if (action === "activate" && confirmations.value[job.id]?.trim() !== expected) {
    error.value = `Type ${expected} exactly before activating. The old index will be preserved.`;
    return;
  }
  activeOperation.value = `${action}:${job.id}`;
  error.value = null;
  notice.value = null;
  try {
    if (
      action === "activate" &&
      !(await controlSession.ensureStepUp("embedding:activate", "/embedding-indexes"))
    ) {
      error.value = "Fresh Telegram verification is required before activating an embedding index.";
      return;
    }
    const updated = await api.request<IndexJob>(
      `/embedding-indexes/jobs/${job.id}${action === "refresh" ? "" : `/${action}`}`,
      action === "refresh"
        ? {}
        : {
            method: "POST",
            body:
              action === "run"
                ? { max_batches: 1 }
                : action === "activate"
                  ? { version: job.version, confirmation: expected }
                  : { version: job.version },
            timeout: action === "run" ? 120_000 : 5_000,
          },
    );
    upsertJob(updated);
    if (action === "activate") {
      confirmations.value[job.id] = "";
      notice.value = `Job ${job.id} activated. Older indexes remain available for a separately validated rollback.`;
      await refreshOverview();
    }
  } catch (caught) {
    error.value = indexError(
      caught,
      `Job ${job.id} could not ${action === "refresh" ? "be refreshed" : action}. Refresh its status before retrying.`,
    );
  } finally {
    activeOperation.value = null;
  }
}

function upsertJob(job: IndexJob) {
  const currentScope = overview.value?.scope ?? preview.value?.scope ?? scope.value.trim();
  if (job.scope !== currentScope) return;
  if (!overview.value) overview.value = { scope: job.scope, active: null, spaces: [], jobs: [] };
  overview.value = {
    ...overview.value,
    jobs: [job, ...overview.value.jobs.filter((candidate) => candidate.id !== job.id)],
  };
}

function indexError(caught: unknown, fallback: string): string {
  return controlResponseStatus(caught) === 409
    ? "Index coverage, route settings or job version changed. Refresh status and preview again before retrying. The active index was not changed by this failed action."
    : controlErrorMessage(caught, fallback);
}
</script>

<template>
  <ProtectedPageGate>
    <template v-if="isOwner">
      <section class="page-introduction" aria-labelledby="embedding-indexes-title">
        <div>
          <p class="eyebrow">Owner-only migration controls</p>
          <h1 id="embedding-indexes-title">Embedding indexes</h1>
          <p class="page-lede">
            Build a compatible replacement in resumable batches, then activate it explicitly. Old
            indexes are preserved. Opening this page does not run inference, download a model or
            start paid work.
          </p>
        </div>
      </section>
      <p v-if="error" class="operation-error" role="alert">{{ error }}</p>
      <p v-if="notice" class="operation-notice" role="status">{{ notice }}</p>
      <section class="management-panel" aria-labelledby="index-scope-title">
        <h2 id="index-scope-title">Scope and current coverage</h2>
        <form class="inline-form" @submit.prevent="loadScope">
          <label for="embedding-scope">Scope</label
          ><input
            id="embedding-scope"
            v-model="scope"
            placeholder="all, chat ID, or chat ID:topic ID"
            :disabled="activeOperation !== null"
          /><UButton type="submit" :disabled="activeOperation !== null">Refresh coverage</UButton>
        </form>
        <template v-if="overview">
          <p v-if="overview.active">
            Active:
            <strong>{{ overview.active.provider_profile }} · {{ overview.active.model }}</strong> ·
            {{ overview.active.dimension }} dimensions · space {{ overview.active.id }}
          </p>
          <p v-else class="empty-panel">
            No verified active index. Legacy vectors remain preserved, but need an explicit
            compatible re-index or evidenced adoption before retrieval can use them.
          </p>
          <div class="management-list">
            <article v-for="space in overview.spaces" :key="space.id" class="management-card">
              <div>
                <strong>{{ space.provider_profile }} · {{ space.model }}</strong
                ><small
                  >Space {{ space.id }} · {{ space.dimension }} dimensions{{
                    overview.active?.id === space.id ? " · active" : " · preserved"
                  }}</small
                >
                <p>
                  {{ space.indexed }} indexed / {{ space.eligible }} eligible ·
                  {{ space.missing }} missing · {{ space.stale }} stale
                </p>
              </div>
            </article>
          </div>
        </template>
      </section>
      <section class="management-panel" aria-labelledby="index-preview-title">
        <h2 id="index-preview-title">Preview a replacement</h2>
        <p>
          Preview counts first. Starting a job does not activate it. Running batches can incur
          provider charges or load a prepared local model. A local-only profile never downloads
          files implicitly.
        </p>
        <form class="profile-form" @submit.prevent="previewIndex">
          <label
            >Embedding provider<select
              v-model="selectedProfile"
              :disabled="activeOperation !== null"
            >
              <option
                v-for="profile in embeddingProfiles"
                :key="profile.name"
                :value="profile.name"
              >
                {{ profile.name }} ·
                {{
                  profile.provider_kind === "local"
                    ? "direct local inference"
                    : profile.local
                      ? "local server"
                      : "remote inference"
                }}
              </option>
            </select></label
          >
          <label
            >Embedding model<input
              v-model="model"
              data-testid="embedding-target-model"
              placeholder="Profile default"
              :disabled="activeOperation !== null"
          /></label>
          <UButton
            data-testid="preview-embedding-index"
            type="button"
            :disabled="!selectedProfile || activeOperation !== null"
            :loading="activeOperation === 'preview'"
            @click="previewIndex"
            >Preview index</UButton
          >
        </form>
        <p v-if="!embeddingProfiles.length" class="empty-panel">
          Configure an enabled embedding-capable profile on the Providers page first.
        </p>
        <div v-if="preview" class="confirmation-panel" aria-live="polite">
          <strong>{{ preview.missing }} sources need indexing</strong>
          <p>
            {{ preview.eligible }} eligible · {{ preview.indexed }} already indexed ·
            {{ preview.provider_profile }} / {{ preview.model }} · scope {{ preview.scope }}
          </p>
          <p v-if="preview.cost_warning">{{ preview.cost_warning }}</p>
          <p v-if="preview.local_preparation_required">
            Local model preparation is required. Use the explicit Providers preparation control
            before running a batch.
          </p>
          <NuxtLink v-if="preview.local_preparation_required" to="/providers"
            >Open Providers</NuxtLink
          >
          <UButton
            v-if="canStart"
            data-testid="start-embedding-job"
            :disabled="activeOperation !== null"
            :loading="activeOperation === 'start'"
            @click="startJob"
            >Start migration job</UButton
          >
        </div>
      </section>
      <section class="management-panel" aria-labelledby="embedding-jobs-title">
        <h2 id="embedding-jobs-title">Resumable jobs</h2>
        <p>
          Progress is refreshed explicitly. Each Run next batch action runs at most one batch. A
          ready job still needs confirmation, fresh verification and a final coverage check before
          activation.
        </p>
        <p v-if="!overview?.jobs.length" class="empty-panel">No migration jobs for this scope.</p>
        <div class="management-list">
          <article v-for="job in overview?.jobs ?? []" :key="job.id" class="management-panel">
            <div class="section-heading">
              <div>
                <h3>Job {{ job.id }} · {{ job.provider_profile }}</h3>
                <p>{{ job.model }} · {{ job.scope }} · v{{ job.version }}</p>
              </div>
              <span class="health-pill" :data-state="job.status">{{ job.status }}</span>
            </div>
            <p role="status">{{ job.processed }} / {{ job.total }} sources processed</p>
            <progress
              :value="job.processed"
              :max="Math.max(job.total, 1)"
              :aria-label="`Job ${job.id} progress`"
            />
            <p v-if="job.error_code" role="alert">
              The last batch failed. Check provider readiness and refresh status before resuming.
              The previous active index is preserved.
            </p>
            <div class="state-actions">
              <UButton
                color="neutral"
                variant="soft"
                :disabled="activeOperation !== null"
                @click="operateJob(job, 'refresh')"
                >Refresh job status</UButton
              >
              <UButton
                v-if="['queued', 'running'].includes(job.status)"
                :data-testid="`run-embedding-job-${job.id}`"
                :disabled="activeOperation !== null"
                :loading="activeOperation === `run:${job.id}`"
                @click="operateJob(job, 'run')"
                >Run next batch</UButton
              >
              <UButton
                v-if="['failed', 'cancelled'].includes(job.status)"
                :data-testid="`resume-embedding-job-${job.id}`"
                :disabled="activeOperation !== null"
                @click="operateJob(job, 'resume')"
                >Resume job</UButton
              >
              <UButton
                v-if="['queued', 'running', 'ready'].includes(job.status)"
                :data-testid="`cancel-embedding-job-${job.id}`"
                color="warning"
                variant="soft"
                :disabled="activeOperation !== null"
                @click="operateJob(job, 'cancel')"
                >Cancel job</UButton
              >
            </div>
            <div v-if="job.status === 'ready'" class="confirmation-panel">
              <p>
                Activating changes the embedding route for {{ job.scope }} and its inheriting
                scopes. Explicit descendant overrides stay unchanged. Coverage and settings will be
                revalidated atomically. Older indexes are not deleted.
              </p>
              <label :for="`embedding-confirmation-${job.id}`">Type ACTIVATE {{ job.id }}</label
              ><input
                :id="`embedding-confirmation-${job.id}`"
                v-model="confirmations[job.id]"
                :data-testid="`embedding-confirmation-${job.id}`"
                autocomplete="off"
                :disabled="activeOperation !== null"
              />
              <UButton
                :data-testid="`activate-embedding-job-${job.id}`"
                :disabled="activeOperation !== null"
                :loading="activeOperation === `activate:${job.id}`"
                @click="operateJob(job, 'activate')"
                >Verify and activate</UButton
              >
            </div>
          </article>
        </div>
      </section>
      <section class="management-panel" aria-labelledby="legacy-adoption-title">
        <h2 id="legacy-adoption-title">Adopt legacy vectors with original provenance</h2>
        <p>
          Browser verification navigates away. Keep your original manifest, then paste it again,
          preview and confirm after returning. Confirmation and attestation are never saved.
        </p>
        <p>
          Use reindexing unless you have original records proving the exact embedding configuration,
          source text fingerprints and vector-byte fingerprints. Matching model names or dimensions
          is not evidence. This operation preserves bytes and does not switch routes.
        </p>
        <form class="profile-form" @submit.prevent="previewLegacyAdoption">
          <label for="legacy-adoption-manifest">Reviewed provenance manifest (JSON)</label>
          <textarea
            id="legacy-adoption-manifest"
            v-model="adoptionManifestText"
            data-testid="legacy-adoption-manifest"
            rows="8"
            maxlength="65536"
            spellcheck="false"
            autocomplete="off"
            :disabled="activeOperation !== null"
          />
          <p>
            Provide scope, provider_profile, model, dimension, original_config_hash and 1–100
            entries containing embedding_id, source_sha256 and vector_sha256. Do not include raw
            text, vectors or credentials.
          </p>
          <UButton
            type="submit"
            data-testid="preview-legacy-adoption"
            :disabled="!adoptionManifestText.trim() || activeOperation !== null"
            :loading="activeOperation === 'legacy-preview'"
            @click.prevent="previewLegacyAdoption"
            >Preview legacy adoption</UButton
          >
        </form>
        <div
          v-if="adoptionPreview"
          class="confirmation-panel legacy-adoption-panel"
          aria-live="polite"
        >
          <strong
            >{{ adoptionPreview.count }} legacy vector{{
              adoptionPreview.count === 1 ? "" : "s"
            }}
            validated for {{ adoptionPreview.scope }}</strong
          >
          <p>
            {{ adoptionPreview.provider_profile }} / {{ adoptionPreview.model }} ·
            {{ adoptionPreview.dimension }} dimensions
          </p>
          <p>
            Configuration fingerprint: <code>{{ adoptionPreview.configuration_fingerprint }}</code>
          </p>
          <label class="legacy-provenance-label"
            ><input
              v-model="provenanceAttested"
              data-testid="legacy-provenance-attested"
              type="checkbox"
              :disabled="activeOperation !== null"
            />I attest that these fingerprints come from original provenance records, not guesses
            based on current settings.</label
          >
          <label for="legacy-adoption-confirmation"
            >Type ADOPT {{ adoptionPreview.preview_hash }}</label
          >
          <input
            id="legacy-adoption-confirmation"
            v-model="adoptionConfirmation"
            data-testid="legacy-adoption-confirmation"
            autocomplete="off"
            :disabled="activeOperation !== null"
          />
          <UButton
            data-testid="commit-legacy-adoption"
            :disabled="!canAdopt || activeOperation !== null"
            :loading="activeOperation === 'legacy-adopt'"
            @click="commitLegacyAdoption"
            >Verify and adopt legacy vectors</UButton
          >
        </div>
      </section>
    </template>
  </ProtectedPageGate>
</template>

<style scoped>
.legacy-adoption-panel {
  min-width: 0;
  overflow-wrap: anywhere;
}
.legacy-adoption-panel input:not([type="checkbox"]) {
  min-width: 0;
  width: 100%;
}
.legacy-provenance-label {
  display: flex;
  align-items: flex-start;
  gap: 0.5rem;
}
.legacy-provenance-label input {
  flex-shrink: 0;
  width: 1rem;
  height: 1rem;
}
</style>
