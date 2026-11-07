<script setup lang="ts">
import type { components } from "~/types/openapi";
import type { ProviderForm, LocalInferenceOptions } from "~/types/providerForms";
import { requiresEmbeddingMigration } from "~/utils/routeSettings";

definePageMeta({ middleware: "auth" });
useSeoMeta({ title: "Providers · PZP AI Control Plane" });

type ProviderProfile = components["schemas"]["ProviderProfileResponse"] &
  Partial<
    Pick<
      ProviderForm,
      "api_dialect" | "auth_mode" | "local_chat_options" | "local_embedding_options"
    >
  >;
type CredentialSummary = components["schemas"]["ProviderCredentialSummary"];
type PoolSummary = components["schemas"]["ProviderPoolSummary"];
type CredentialTestResult = components["schemas"]["ProviderCredentialTestResponse"];
type AdminAlertPage = components["schemas"]["AdminAlertPage"];
type AdminAlert = components["schemas"]["AdminAlertSummary"];

interface CredentialDraft {
  label: string;
  position: number;
  state: string;
}

const defaultProfileDraft = (): ProviderForm => ({
  name: "",
  provider_kind: "openai_compatible",
  base_url: "",
  chat_model: "gpt-5.6-terra",
  embedding_model: "text-embedding-3-small",
  chat_capable: true,
  embedding_capable: true,
  reasoning_capable: true,
  local: false,
  redact_before_ai: true,
  fake_embedding_dimensions: 64,
  api_dialect: "auto",
  auth_mode: "api_key",
  local_chat_options: {
    device: "auto",
    dtype: "auto",
    local_files_only: true,
    max_input_tokens: 4096,
    max_new_tokens: 1024,
    top_p: 1,
    queue_timeout_seconds: 30,
    inference_timeout_seconds: 120,
  },
  local_embedding_options: {
    device: "auto",
    local_files_only: true,
    batch_size: 32,
    normalize_embeddings: true,
    prompt_mode: "generic",
  },
});

const api = useControlApi();
const controlSession = useControlSession();
const profiles = ref<ProviderProfile[]>([]);
const selectedProfileName = ref("");
const credentials = ref<CredentialSummary[]>([]);
const pool = ref<PoolSummary | null>(null);
const alerts = ref<AdminAlert[]>([]);
const newProfile = ref<ProviderForm>(defaultProfileDraft());
const profileEdit = ref<ProviderForm | null>(null);
const profileEditBaseline = ref("");
const newCredentialLabel = ref("");
const newCredentialValue = ref("");
const credentialDrafts = ref<Record<number, CredentialDraft>>({});
const replacementLabels = ref<Record<number, string>>({});
const replacementValues = ref<Record<number, string>>({});
const pendingDelete = ref<string | null>(null);
const loading = ref(false);
const error = ref<string | null>(null);
const notice = ref<string | null>(null);
const migrationRequired = ref(false);
const profileTestResults = ref<Partial<Record<"chat" | "embedding", boolean>>>({});
const allowProfileDownload = ref(false);
const activeOperation = ref<string | null>(null);
const testResults = ref<Record<number, CredentialTestResult>>({});

const selectedProfile = computed(
  () => profiles.value.find((profile) => profile.name === selectedProfileName.value) ?? null,
);
const requiresCredentials = computed(() =>
  Boolean(
    selectedProfile.value &&
    selectedProfile.value.provider_kind !== "local" &&
    selectedProfile.value.auth_mode !== "none",
  ),
);
const dirty = computed(
  () =>
    newProfile.value.name.trim() !== "" ||
    newCredentialLabel.value.trim() !== "" ||
    newCredentialValue.value !== "" ||
    Object.values(replacementValues.value).some(Boolean) ||
    Boolean(profileEdit.value && JSON.stringify(profileEdit.value) !== profileEditBaseline.value),
);

onMounted(async () => {
  if (!controlSession.hasCapability("bot.admin")) {
    await navigateTo("/unauthorized");
    return;
  }
  await refreshPage();
});
watch(dirty, (value) => syncTelegramClosingConfirmation(window.Telegram?.WebApp, value));
onBeforeUnmount(() => syncTelegramClosingConfirmation(window.Telegram?.WebApp, false));

async function refreshPage() {
  loading.value = true;
  error.value = null;
  try {
    profiles.value = await api.request<ProviderProfile[]>("/providers");
    if (!profiles.value.some((profile) => profile.name === selectedProfileName.value))
      selectedProfileName.value = profiles.value[0]?.name ?? "";
    await Promise.all([loadSelectedProfile(), loadAlerts()]);
  } catch (caught) {
    error.value = controlErrorMessage(caught, "Provider operations could not be loaded.");
  } finally {
    loading.value = false;
  }
}

async function loadSelectedProfile() {
  const profile = selectedProfile.value;
  profileTestResults.value = {};
  allowProfileDownload.value = false;
  syncProfileDraft(profile);
  if (!profile || !requiresCredentials.value) {
    credentials.value = [];
    pool.value = null;
    return;
  }
  const profileName = encodeURIComponent(profile.name);
  credentials.value = await api.request<CredentialSummary[]>(
    `/providers/${profileName}/credentials`,
  );
  credentialDrafts.value = Object.fromEntries(
    credentials.value
      .filter((item): item is CredentialSummary & { id: number } => typeof item.id === "number")
      .map((item) => [item.id, { label: item.label, position: item.position, state: item.state }]),
  );
  replacementLabels.value = Object.fromEntries(
    credentials.value
      .filter((item): item is CredentialSummary & { id: number } => typeof item.id === "number")
      .map((item) => [item.id, item.label]),
  );
  pool.value =
    profile.source === "managed"
      ? await api.request<PoolSummary>(`/providers/${profileName}/pool`)
      : null;
}

function syncProfileDraft(profile: ProviderProfile | null) {
  if (!profile || profile.source !== "managed") {
    profileEdit.value = null;
    profileEditBaseline.value = "";
    return;
  }
  profileEdit.value = {
    name: profile.name,
    provider_kind: profile.provider_kind,
    base_url: profile.base_url,
    chat_model: profile.chat_model,
    embedding_model: profile.embedding_model,
    chat_capable: profile.chat_capable,
    embedding_capable: profile.embedding_capable,
    reasoning_capable: profile.reasoning_capable,
    local: profile.local,
    redact_before_ai: profile.redact_before_ai,
    fake_embedding_dimensions: profile.fake_embedding_dimensions,
    api_dialect: profile.api_dialect ?? "auto",
    auth_mode: profile.auth_mode ?? (profile.provider_kind === "local" ? "none" : "api_key"),
    local_chat_options: {
      ...defaultProfileDraft().local_chat_options,
      ...profile.local_chat_options,
    },
    local_embedding_options: {
      ...defaultProfileDraft().local_embedding_options,
      ...profile.local_embedding_options,
    },
  };
  profileEditBaseline.value = JSON.stringify(profileEdit.value);
}

async function loadAlerts() {
  const page = await api.request<AdminAlertPage>("/alerts?limit=25");
  alerts.value = page.items;
}

async function createProfile() {
  const payload = {
    ...profilePayload(newProfile.value),
    name: newProfile.value.name.trim(),
    base_url: newProfile.value.base_url.trim(),
    redaction_patterns: [] as string[],
  };
  if (!payload.name) {
    error.value = "Enter a managed profile name.";
    return;
  }
  if (payload.provider_kind === "openai_compatible" && !payload.base_url) {
    error.value = "Enter the provider base URL.";
    return;
  }
  activeOperation.value = "profile:create";
  error.value = null;
  try {
    const created = await api.request<ProviderProfile>("/providers", {
      method: "POST",
      body: payload,
    });
    profiles.value = [
      ...profiles.value.filter((profile) => profile.name !== created.name),
      created,
    ].sort((left, right) => left.name.localeCompare(right.name));
    selectedProfileName.value = created.name;
    newProfile.value = defaultProfileDraft();
    await loadSelectedProfile();
    notice.value = requiresCredentials.value
      ? `${created.name} is ready for an ordered credential pool.`
      : `${created.name} saved. No credential is inherited and no inference was started.`;
  } catch (caught) {
    error.value = controlErrorMessage(caught, "The managed profile could not be created.");
  } finally {
    activeOperation.value = null;
  }
}

async function saveProfile() {
  const profile = selectedProfile.value;
  if (!profile || !profileEdit.value) return;
  activeOperation.value = "profile:update";
  error.value = null;
  migrationRequired.value = false;
  try {
    const draft = profileEdit.value;
    const updated = await api.request<ProviderProfile>(
      `/providers/${encodeURIComponent(profile.name)}`,
      {
        method: "PATCH",
        body: {
          version: profile.version,
          ...profilePayload(draft),
        },
      },
    );
    replaceProfile(updated);
    await loadSelectedProfile();
    notice.value = `${updated.name} updated without a bot restart.`;
  } catch (caught) {
    migrationRequired.value = requiresEmbeddingMigration(caught);
    error.value = migrationRequired.value
      ? "This profile is used by indexed memory. Create a replacement profile and preview an embedding-index migration before changing its embedding model or backend."
      : controlErrorMessage(caught, "The provider profile could not be updated.");
  } finally {
    activeOperation.value = null;
  }
}

async function toggleProfile() {
  const profile = selectedProfile.value;
  if (!profile || profile.source !== "managed") return;
  activeOperation.value = "profile:toggle";
  try {
    const updated = await api.request<ProviderProfile>(
      `/providers/${encodeURIComponent(profile.name)}`,
      { method: "PATCH", body: { version: profile.version, enabled: !profile.enabled } },
    );
    replaceProfile(updated);
    syncProfileDraft(updated);
  } catch (caught) {
    error.value = controlErrorMessage(caught, "The provider profile state could not be changed.");
  } finally {
    activeOperation.value = null;
  }
}

async function deleteProfile() {
  const profile = selectedProfile.value;
  if (!profile || profile.source !== "managed") return;
  if (!(await controlSession.ensureStepUp("profile:delete", "/providers"))) return;
  activeOperation.value = "profile:delete";
  try {
    await api.request(`/providers/${encodeURIComponent(profile.name)}`, {
      method: "DELETE",
      body: { version: profile.version },
    });
    profiles.value = profiles.value.filter((candidate) => candidate.name !== profile.name);
    selectedProfileName.value = profiles.value[0]?.name ?? "";
    pendingDelete.value = null;
    await loadSelectedProfile();
  } catch (caught) {
    error.value = controlErrorMessage(caught, "The provider profile could not be deleted.");
  } finally {
    activeOperation.value = null;
  }
}

async function createCredential() {
  const profile = selectedProfile.value;
  if (!profile || profile.source !== "managed") return;
  if (!(await controlSession.ensureStepUp("credential:create", "/providers"))) return;
  activeOperation.value = "credential:create";
  error.value = null;
  try {
    await api.request(`/providers/${encodeURIComponent(profile.name)}/credentials`, {
      method: "POST",
      body: { label: newCredentialLabel.value.trim(), value: newCredentialValue.value },
    });
    newCredentialLabel.value = "";
    newCredentialValue.value = "";
    await loadSelectedProfile();
    notice.value = "Credential encrypted and added to the pool.";
  } catch (caught) {
    error.value = controlErrorMessage(caught, "The provider credential could not be created.");
  } finally {
    activeOperation.value = null;
  }
}

async function updateCredential(credential: CredentialSummary) {
  if (typeof credential.id !== "number") return;
  const draft = credentialDrafts.value[credential.id];
  if (!draft) return;
  activeOperation.value = `credential:update:${credential.id}`;
  try {
    await api.request(
      `/providers/${encodeURIComponent(credential.profile_name)}/credentials/${credential.id}`,
      {
        method: "PATCH",
        body: {
          version: credential.version,
          label: draft.label,
          position: draft.position,
          state: draft.state,
        },
      },
    );
    await loadSelectedProfile();
  } catch (caught) {
    error.value = controlErrorMessage(
      caught,
      "The credential order or state could not be updated.",
    );
  } finally {
    activeOperation.value = null;
  }
}

async function replaceCredential(credential: CredentialSummary) {
  if (typeof credential.id !== "number") return;
  const value = replacementValues.value[credential.id] ?? "";
  if (!value) {
    error.value = "Enter the replacement credential value.";
    return;
  }
  if (!(await controlSession.ensureStepUp("credential:replace", "/providers"))) return;
  activeOperation.value = `credential:replace:${credential.id}`;
  try {
    await api.request(
      `/providers/${encodeURIComponent(credential.profile_name)}/credentials/${credential.id}/replace`,
      {
        method: "POST",
        body: {
          version: credential.version,
          label: replacementLabels.value[credential.id] ?? credential.label,
          value,
        },
      },
    );
    replacementValues.value[credential.id] = "";
    await loadSelectedProfile();
    notice.value = `${credential.label} was atomically re-encrypted and its health history reset.`;
  } catch (caught) {
    error.value = controlErrorMessage(caught, "The credential could not be replaced.");
  } finally {
    activeOperation.value = null;
  }
}

async function deleteCredential(credential: CredentialSummary) {
  if (typeof credential.id !== "number") return;
  if (!(await controlSession.ensureStepUp("credential:delete", "/providers"))) return;
  activeOperation.value = `credential:delete:${credential.id}`;
  try {
    await api.request(
      `/providers/${encodeURIComponent(credential.profile_name)}/credentials/${credential.id}`,
      { method: "DELETE", body: { version: credential.version } },
    );
    pendingDelete.value = null;
    await loadSelectedProfile();
  } catch (caught) {
    error.value = controlErrorMessage(caught, "The credential could not be deleted.");
  } finally {
    activeOperation.value = null;
  }
}

async function runCredentialTest(credential: CredentialSummary, mode: "chat" | "embedding") {
  if (typeof credential.id !== "number") return;
  const operation = `${mode}:${credential.id}`;
  activeOperation.value = operation;
  error.value = null;
  try {
    const result = await api.request<CredentialTestResult>(
      `/providers/${encodeURIComponent(credential.profile_name)}/credentials/${credential.id}/test`,
      { body: { mode }, method: "POST" },
    );
    testResults.value = { ...testResults.value, [credential.id]: result };
    await loadSelectedProfile();
  } catch (caught) {
    error.value = controlErrorMessage(
      caught,
      "The manual provider check failed. Credential health has been refreshed.",
    );
    await loadSelectedProfile();
  } finally {
    activeOperation.value = null;
  }
}

async function runProfileTest(mode: "chat" | "embedding") {
  const profile = selectedProfile.value;
  if (!profile || activeOperation.value !== null) return;
  activeOperation.value = `profile:test:${mode}`;
  error.value = null;
  profileTestResults.value = { ...profileTestResults.value, [mode]: undefined };
  try {
    const result = await api.request<{
      success: boolean;
      mode: "chat" | "embedding";
      detail: string;
    }>(`/providers/${encodeURIComponent(profile.name)}/test`, {
      method: "POST",
      body: { mode },
      timeout: 120_000,
    });
    if (
      selectedProfile.value?.name === profile.name &&
      selectedProfile.value.version === profile.version
    )
      profileTestResults.value = { ...profileTestResults.value, [mode]: result.success };
  } catch (caught) {
    error.value = controlErrorMessage(
      caught,
      "The explicit provider check failed. Check the configured model and provider readiness.",
    );
  } finally {
    activeOperation.value = null;
  }
}

async function prepareLocalProfile(mode: "chat" | "embedding") {
  const profile = selectedProfile.value;
  if (!profile || profile.provider_kind !== "local" || activeOperation.value !== null) return;
  activeOperation.value = `profile:prepare:${mode}`;
  error.value = null;
  notice.value = null;
  const allowDownload = allowProfileDownload.value;
  try {
    if (
      allowDownload &&
      !(await controlSession.ensureStepUp("local_model:download", "/providers"))
    ) {
      error.value = "Fresh verification is required before downloading model files.";
      return;
    }
    const result = await api.request<{
      provider_profile: string;
      mode: string;
      loaded: boolean;
      downloaded: boolean;
      download_bytes: number;
    }>(`/providers/${encodeURIComponent(profile.name)}/prepare-local`, {
      method: "POST",
      body: { mode, allow_download: allowDownload },
      timeout: 120_000,
    });
    notice.value = result.loaded
      ? `${profile.name} ${mode} model ${result.downloaded ? `prepared after an explicit bounded download (${result.download_bytes} bytes)` : "prepared from local files"}. Run an explicit test to verify inference.`
      : "The model was not loaded. Check local dependencies and deployment-approved model/cache paths before retrying.";
  } catch (caught) {
    error.value = controlErrorMessage(
      caught,
      "Local model preparation failed. Check local dependencies, pinned revisions and deployment-approved model/cache roots.",
    );
  } finally {
    activeOperation.value = null;
  }
}

async function retryAlert(alertId: number) {
  activeOperation.value = `alert:${alertId}`;
  try {
    await api.request(`/alerts/${alertId}/retry`, { method: "POST" });
    await loadAlerts();
  } catch (caught) {
    error.value = controlErrorMessage(caught, "The alert could not be queued for retry.");
  } finally {
    activeOperation.value = null;
  }
}

function replaceProfile(updated: ProviderProfile) {
  profiles.value = profiles.value.map((profile) =>
    profile.name === updated.name ? updated : profile,
  );
}

function profilePayload(draft: ProviderForm) {
  const cleanOptions = (options: LocalInferenceOptions) =>
    Object.fromEntries(
      Object.entries(options).filter(([, value]) => value !== "" && value !== undefined),
    );
  return {
    provider_kind: draft.provider_kind,
    base_url: draft.base_url.trim(),
    chat_model: draft.chat_capable ? draft.chat_model.trim() : "",
    embedding_model: draft.embedding_capable ? draft.embedding_model.trim() : "",
    chat_capable: draft.chat_capable,
    embedding_capable: draft.embedding_capable,
    reasoning_capable: draft.chat_capable && draft.reasoning_capable,
    local: draft.provider_kind === "local" || draft.local,
    auth_mode: draft.provider_kind === "local" ? "none" : draft.auth_mode,
    api_dialect: draft.api_dialect,
    redact_before_ai: draft.redact_before_ai,
    fake_embedding_dimensions: draft.fake_embedding_dimensions,
    local_chat_options: cleanOptions(draft.local_chat_options),
    local_embedding_options: cleanOptions(draft.local_embedding_options),
  };
}

function formatTimestamp(value: string | null): string {
  return value
    ? new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(
        new Date(value),
      )
    : "Never";
}
</script>

<template>
  <ProtectedPageGate>
    <section class="page-introduction" aria-labelledby="providers-title">
      <div>
        <p class="eyebrow">Provider resilience</p>
        <h1 id="providers-title">Keys that fail without taking the bot down.</h1>
        <p class="page-lede">
          Manage profiles and ordered encrypted key pools without a restart. Plaintext is accepted
          or revealed only through explicit, short-lived, audited actions.
        </p>
      </div>
      <UButton color="neutral" variant="soft" :loading="loading" @click="refreshPage"
        >Refresh</UButton
      >
    </section>
    <div class="provider-warning-grid">
      <UAlert
        color="warning"
        variant="soft"
        title="Provider operations may incur usage charges"
        description="Chat and embedding tests run only when explicitly selected, using a fixed non-sensitive input."
      /><UAlert
        color="info"
        variant="soft"
        title="Local model checks may download model files"
        description="No check runs automatically. Prepare cached files or explicitly allow a bounded model download below."
      />
    </div>
    <p v-if="error" class="operation-error" role="alert">{{ error }}</p>
    <NuxtLink v-if="migrationRequired" to="/embedding-indexes">Open Embedding indexes</NuxtLink>
    <p v-if="notice" class="operation-notice" aria-live="polite">{{ notice }}</p>

    <section class="provider-console" aria-labelledby="create-profile-title">
      <div class="section-heading">
        <div>
          <p class="eyebrow">Managed configuration</p>
          <h2 id="create-profile-title">Create managed profile</h2>
        </div>
        <span>Database-backed · reloadless</span>
      </div>
      <form class="profile-form" @submit.prevent="createProfile">
        <label
          >Name<input v-model="newProfile.name" data-testid="new-profile-name" autocomplete="off"
        /></label>
        <ProviderProfileFields v-model="newProfile" prefix="new-profile" />
        <UButton
          data-testid="create-profile"
          type="button"
          :loading="activeOperation === 'profile:create'"
          @click="createProfile"
          >Create profile</UButton
        >
      </form>
    </section>

    <section v-if="profiles.length" class="provider-console" aria-labelledby="pool-title">
      <div class="provider-toolbar">
        <label for="provider-profile">Provider profile</label
        ><select
          id="provider-profile"
          v-model="selectedProfileName"
          :disabled="activeOperation !== null"
          @change="loadSelectedProfile"
        >
          <option v-for="profile in profiles" :key="profile.name" :value="profile.name">
            {{ profile.name }}
          </option></select
        ><span v-if="selectedProfile" class="provider-models"
          >{{ selectedProfile.chat_model }} · {{ selectedProfile.embedding_model }}</span
        ><span
          v-if="selectedProfile"
          class="health-pill"
          :data-state="selectedProfile.enabled ? 'healthy' : 'disabled'"
          >{{ selectedProfile.enabled ? "enabled" : "disabled" }}</span
        >
      </div>
      <p v-if="selectedProfile">
        Capabilities: {{ selectedProfile.chat_capable ? "answers and summaries" : "no chat" }} ·
        {{ selectedProfile.embedding_capable ? "embeddings" : "no embeddings" }} ·
        {{
          selectedProfile.provider_kind === "local"
            ? "direct local inference"
            : selectedProfile.local
              ? "local inference server"
              : "remote/test backend"
        }}. Readiness is checked only through explicit preparation or test actions.
      </p>
      <div v-if="selectedProfile" class="management-panel">
        <h2>Explicit profile checks</h2>
        <p>
          Checks use fixed non-sensitive input and may incur usage charges. They do not change route
          selection or activate an index. No check runs on page load.
        </p>
        <div class="state-actions">
          <UButton
            v-if="selectedProfile.chat_capable"
            data-testid="test-profile-chat"
            :disabled="activeOperation !== null"
            :loading="activeOperation === 'profile:test:chat'"
            @click="runProfileTest('chat')"
            >Test profile chat</UButton
          >
          <UButton
            v-if="selectedProfile.embedding_capable"
            data-testid="test-profile-embedding"
            :disabled="activeOperation !== null"
            :loading="activeOperation === 'profile:test:embedding'"
            @click="runProfileTest('embedding')"
            >Test profile embedding</UButton
          >
        </div>
        <p
          v-for="mode in ['chat', 'embedding'] as const"
          v-show="profileTestResults[mode] !== undefined"
          :key="mode"
          role="status"
        >
          {{ mode === "chat" ? "Chat" : "Embedding" }} check
          {{ profileTestResults[mode] ? "passed" : "did not pass, check provider readiness" }}
        </p>
        <template v-if="selectedProfile.provider_kind === 'local'">
          <label
            ><input
              v-model="allowProfileDownload"
              data-testid="allow-profile-download"
              type="checkbox"
              :disabled="activeOperation !== null"
            />
            Allow a bounded model download (requires fresh verification)</label
          >
          <div class="state-actions">
            <UButton
              v-if="selectedProfile.chat_capable"
              data-testid="prepare-profile-chat"
              color="neutral"
              variant="soft"
              :disabled="activeOperation !== null"
              :loading="activeOperation === 'profile:prepare:chat'"
              @click="prepareLocalProfile('chat')"
              >{{
                allowProfileDownload ? "Download and prepare chat" : "Prepare cached chat"
              }}</UButton
            >
            <UButton
              v-if="selectedProfile.embedding_capable"
              data-testid="prepare-profile-embedding"
              color="neutral"
              variant="soft"
              :disabled="activeOperation !== null"
              :loading="activeOperation === 'profile:prepare:embedding'"
              @click="prepareLocalProfile('embedding')"
              >{{
                allowProfileDownload ? "Download and prepare embedding" : "Prepare cached embedding"
              }}</UButton
            >
          </div>
        </template>
      </div>
      <p v-if="selectedProfile?.source !== 'managed'" class="empty-panel">
        Environment bootstrap profiles stay deployment-managed and immutable. Create a managed
        overlay to enable pool mutation.
      </p>
      <form
        v-if="selectedProfile?.source === 'managed' && profileEdit"
        class="profile-form edit-profile"
        @submit.prevent="saveProfile"
      >
        <ProviderProfileFields v-model="profileEdit" prefix="edit-profile" />
        <div class="state-actions">
          <UButton
            type="button"
            :loading="activeOperation === 'profile:update'"
            @click="saveProfile"
            >Save profile</UButton
          ><UButton
            type="button"
            color="warning"
            variant="soft"
            :loading="activeOperation === 'profile:toggle'"
            @click="toggleProfile"
            >{{ selectedProfile.enabled ? "Disable" : "Enable" }}</UButton
          ><UButton
            type="button"
            color="error"
            variant="soft"
            @click="pendingDelete = `profile:${selectedProfile.name}`"
            >Delete profile</UButton
          >
        </div>
      </form>
      <div
        v-if="pendingDelete === `profile:${selectedProfile?.name}`"
        class="confirmation-panel"
        role="alertdialog"
      >
        <strong>Delete profile {{ selectedProfile?.name }} and its managed credentials?</strong>
        <div class="state-actions">
          <UButton
            color="error"
            :loading="activeOperation === 'profile:delete'"
            @click="deleteProfile"
            >Confirm profile deletion</UButton
          ><UButton color="neutral" variant="ghost" @click="pendingDelete = null">Cancel</UButton>
        </div>
      </div>

      <div v-if="pool" class="pool-summary-grid">
        <article>
          <span>Eligible</span
          ><strong>{{ pool.eligible_credentials }} / {{ pool.total_credentials }}</strong>
        </article>
        <article>
          <span>Active key</span><strong>{{ pool.active_credential_label ?? "None" }}</strong>
        </article>
        <article><span>Cooldown</span><strong>{{ pool.cooldown_credentials }}</strong></article>
        <article><span>Disabled</span><strong>{{ pool.disabled_credentials }}</strong></article>
        <article><span>Health epoch</span><strong>{{ pool.health_epoch }}</strong></article>
        <article>
          <span>Last recovery</span><strong>{{ formatTimestamp(pool.last_recovered_at) }}</strong>
        </article>
      </div>
      <p
        v-if="pool && pool.total_credentials > 0 && pool.eligible_credentials === 0"
        class="operation-error"
      >
        This pool is exhausted. Add or re-enable a healthy key; the bot will return one bounded
        provider-unavailable response meanwhile.
      </p>

      <form
        v-if="selectedProfile?.source === 'managed' && requiresCredentials"
        class="credential-create-form"
        @submit.prevent="createCredential"
      >
        <div>
          <p class="eyebrow">Encrypted pool entry</p>
          <h2>Add credential</h2>
        </div>
        <label
          >Label<input
            v-model="newCredentialLabel"
            data-testid="new-credential-label"
            autocomplete="off" /></label
        ><label
          >Secret value<input
            v-model="newCredentialValue"
            data-testid="new-credential-value"
            type="password"
            autocomplete="new-password" /></label
        ><UButton
          data-testid="create-credential"
          type="button"
          :loading="activeOperation === 'credential:create'"
          @click="createCredential"
          >Encrypt and add</UButton
        >
      </form>

      <p v-if="!requiresCredentials" class="empty-panel">
        This local profile does not use a credential pool. No remote default key is inherited. Use
        the explicit profile controls to prepare or test local inference.
      </p>
      <div v-if="requiresCredentials" class="section-heading">
        <div>
          <p class="eyebrow">Ordered pool</p>
          <h2 id="pool-title">Credential health and controls</h2>
        </div>
        <span>{{ credentials.length }} configured</span>
      </div>
      <div v-if="requiresCredentials && credentials.length" class="credential-list">
        <article v-for="credential in credentials" :key="credential.id" class="credential-card">
          <header>
            <div>
              <strong>{{ credential.label }}</strong
              ><small
                >{{ credential.source }} · {{ credential.fingerprint ?? "no fingerprint" }} ·
                position {{ credential.position }}</small
              >
            </div>
            <span class="health-pill" :data-state="credential.health_state"
              >{{ credential.active ? "active · " : "" }}{{ credential.health_state }}</span
            >
          </header>
          <ProviderCredentialSecret :credential="credential" />
          <dl class="credential-metrics">
            <div>
              <dt>Successes</dt>
              <dd>{{ credential.success_count }}</dd>
            </div>
            <div>
              <dt>Failures</dt>
              <dd>{{ credential.failure_count }}</dd>
            </div>
            <div>
              <dt>Last success</dt>
              <dd>{{ formatTimestamp(credential.last_success_at) }}</dd>
            </div>
            <div>
              <dt>Last failure</dt>
              <dd>{{ formatTimestamp(credential.last_failure_at) }}</dd>
            </div>
          </dl>
          <template v-if="typeof credential.id === 'number' && credentialDrafts[credential.id]"
            ><div class="credential-edit-grid">
              <label>Label<input v-model="credentialDrafts[credential.id]!.label" /></label
              ><label
                >Position<input
                  v-model.number="credentialDrafts[credential.id]!.position"
                  type="number"
                  min="0" /></label
              ><label
                >State<select v-model="credentialDrafts[credential.id]!.state">
                  <option value="enabled">Enabled</option>
                  <option value="disabled">Disabled</option>
                </select></label
              ><UButton
                size="sm"
                color="neutral"
                variant="soft"
                :loading="activeOperation === `credential:update:${credential.id}`"
                @click="updateCredential(credential)"
                >Save order/state</UButton
              >
            </div>
            <div class="credential-replace-grid">
              <label
                >Replacement label<input
                  v-model="replacementLabels[credential.id]"
                  :data-testid="`replace-credential-label-${credential.id}`" /></label
              ><label
                >Replacement secret<input
                  v-model="replacementValues[credential.id]"
                  :data-testid="`replace-credential-value-${credential.id}`"
                  type="password"
                  autocomplete="new-password" /></label
              ><UButton
                :data-testid="`replace-credential-${credential.id}`"
                size="sm"
                color="warning"
                variant="soft"
                :loading="activeOperation === `credential:replace:${credential.id}`"
                @click="replaceCredential(credential)"
                >Replace atomically</UButton
              >
            </div>
            <div class="credential-actions">
              <UButton
                v-if="selectedProfile?.chat_capable"
                :data-testid="`test-chat-${credential.id}`"
                size="sm"
                :loading="activeOperation === `chat:${credential.id}`"
                @click="runCredentialTest(credential, 'chat')"
                >Test chat</UButton
              ><UButton
                v-if="selectedProfile?.embedding_capable"
                :data-testid="`test-embedding-${credential.id}`"
                size="sm"
                color="neutral"
                variant="soft"
                :loading="activeOperation === `embedding:${credential.id}`"
                @click="runCredentialTest(credential, 'embedding')"
                >Test embedding</UButton
              ><UButton
                size="sm"
                color="error"
                variant="soft"
                @click="pendingDelete = `credential:${credential.id}`"
                >Delete</UButton
              >
            </div>
            <p v-if="testResults[credential.id]" class="test-result">
              {{ testResults[credential.id]?.model }} passed ·
              {{ testResults[credential.id]?.final_classification }}
            </p>
            <div v-if="pendingDelete === `credential:${credential.id}`" class="confirmation-panel">
              <strong>Delete {{ credential.label }}?</strong>
              <div class="state-actions">
                <UButton
                  color="error"
                  :loading="activeOperation === `credential:delete:${credential.id}`"
                  @click="deleteCredential(credential)"
                  >Confirm credential deletion</UButton
                ><UButton color="neutral" variant="ghost" @click="pendingDelete = null"
                  >Cancel</UButton
                >
              </div>
            </div></template
          >
        </article>
      </div>
      <p
        v-else-if="requiresCredentials && selectedProfile?.source === 'managed'"
        class="empty-panel"
      >
        No credentials are configured for this managed profile.
      </p>
      <p v-else-if="requiresCredentials" class="empty-panel">
        No credentials are configured for this environment bootstrap profile.
      </p>
    </section>

    <section class="alert-history" aria-labelledby="alerts-title">
      <div class="section-heading">
        <div>
          <p class="eyebrow">Telegram delivery</p>
          <h2 id="alerts-title">Sanitized admin alerts</h2>
        </div>
        <span>{{ alerts.length }} recent</span>
      </div>
      <div v-if="alerts.length" class="alert-list">
        <article v-for="alert in alerts" :key="alert.id">
          <div>
            <strong>{{ alert.alert_type }}</strong
            ><small
              >{{ alert.profile_name }} · epoch {{ alert.health_epoch }} ·
              {{ formatTimestamp(alert.created_at) }}</small
            >
          </div>
          <span class="health-pill" :data-state="alert.state">{{ alert.state }}</span
          ><UButton
            v-if="alert.state !== 'delivered'"
            :data-testid="`retry-alert-${alert.id}`"
            size="xs"
            color="neutral"
            variant="ghost"
            :loading="activeOperation === `alert:${alert.id}`"
            @click="retryAlert(alert.id)"
            >Retry</UButton
          >
        </article>
      </div>
      <p v-else class="empty-panel">No provider alerts have been recorded.</p>
    </section>
  </ProtectedPageGate>
</template>
