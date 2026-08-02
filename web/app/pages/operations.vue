<script setup lang="ts">
import type { components } from "../types/openapi";

definePageMeta({ middleware: "auth" });
useSeoMeta({ title: "Operations · PZP AI Control Plane" });

type OperationsHealth = components["schemas"]["OperationsHealthResponse"];
type OperationCheck = components["schemas"]["OperationCheckResponse"];
type QueryCatalog = components["schemas"]["QueryCatalogResponse"];

const api = useControlApi();
const controlSession = useControlSession();
const health = ref<OperationsHealth | null>(null);
const catalog = ref<QueryCatalog | null>(null);
const providerProfile = ref("");
const providerMode = ref<"chat" | "embedding">("chat");
const localProfile = ref("");
const allowDownload = ref(false);
const results = ref<Record<string, OperationCheck>>({});
const loading = ref(true);
const activeCheck = ref<string | null>(null);
const error = ref<string | null>(null);

const providerProfiles = computed(
  () => catalog.value?.profiles.filter((profile) => profile.enabled && !profile.local_state) ?? [],
);
const localProfiles = computed(
  () => catalog.value?.profiles.filter((profile) => profile.enabled && profile.local_state) ?? [],
);
const providerState = computed(
  () =>
    (health.value?.providers ?? {}) as {
      profiles: number;
      credentials: { eligible: number; total: number };
    },
);
const alertStates = computed(() => (health.value?.alerts.states ?? {}) as Record<string, number>);

onMounted(async () => {
  if (!controlSession.hasCapability("bot.admin")) {
    await navigateTo("/unauthorized");
    return;
  }
  await loadOperations();
});

async function loadOperations() {
  loading.value = true;
  error.value = null;
  try {
    [health.value, catalog.value] = await Promise.all([
      api.request<OperationsHealth>("/operations/health"),
      api.request<QueryCatalog>("/query/catalog?scope=all"),
    ]);
    providerProfile.value =
      providerProfiles.value.find((profile) => profile.name === providerProfile.value)?.name ??
      providerProfiles.value[0]?.name ??
      "";
    localProfile.value =
      localProfiles.value.find((profile) => profile.name === localProfile.value)?.name ??
      localProfiles.value[0]?.name ??
      "";
  } catch (caught) {
    error.value = controlErrorMessage(caught, "Operational status could not be loaded.");
  } finally {
    loading.value = false;
  }
}

async function runCheck(name: string, body: Record<string, unknown> = {}) {
  activeCheck.value = name;
  error.value = null;
  try {
    results.value = {
      ...results.value,
      [name]: await api.request<OperationCheck>(`/operations/checks/${name}`, {
        method: "POST",
        body,
      }),
    };
    await loadHealthOnly();
  } catch (caught) {
    error.value = controlErrorMessage(caught, `The ${name} check failed.`);
  } finally {
    activeCheck.value = null;
  }
}

async function loadHealthOnly() {
  health.value = await api.request<OperationsHealth>("/operations/health");
}

function runProviderCheck() {
  return runCheck("provider-profile", {
    profile_name: providerProfile.value,
    mode: providerMode.value,
    allow_download: false,
  });
}

function runLocalCheck() {
  return runCheck("local-model-readiness", {
    profile_name: localProfile.value,
    mode: null,
    allow_download: allowDownload.value,
  });
}
</script>

<template>
  <ProtectedPageGate>
    <section class="page-introduction" aria-labelledby="operations-title">
      <div>
        <p class="eyebrow">Explicit diagnostics</p>
        <h1 id="operations-title">Observe first. Trigger second.</h1>
        <p class="page-lede">
          Cached health is side-effect free. Database and projection checks are local; provider
          checks may incur usage, and local readiness can download model files only when you opt in.
        </p>
      </div>
      <UButton color="neutral" variant="soft" :loading="loading" @click="loadOperations"
        >Refresh cached status</UButton
      >
    </section>
    <div class="provider-warning-grid">
      <UAlert
        color="warning"
        variant="soft"
        title="Provider check may incur provider usage charges"
        description="It sends one fixed non-sensitive input only after you press Run provider check."
      /><UAlert
        color="info"
        variant="soft"
        title="Local readiness may download model files"
        description="Download remains disabled until you explicitly select the checkbox and run the local check."
      />
    </div>
    <p v-if="loading && !health" class="empty-panel" role="status">
      Loading cached operational state…
    </p>
    <p v-if="error" class="operation-error" role="alert">{{ error }}</p>

    <section v-if="health" class="observability-panel" aria-labelledby="cached-health-title">
      <div class="section-heading">
        <div>
          <p class="eyebrow">Cached status · No provider call performed</p>
          <h2 id="cached-health-title">Runtime, storage, pools and alerts</h2>
        </div>
        <span>{{ health.generated_at }}</span>
      </div>
      <div class="metric-grid compact">
        <article class="metric-card">
          <span>Schema</span><strong>{{ health.runtime.schema_revision }}</strong
          ><small>{{ health.runtime.uptime_seconds }} seconds uptime</small>
        </article>
        <article class="metric-card">
          <span>Provider profiles</span><strong>{{ providerState.profiles }}</strong
          ><small
            >{{ providerState.credentials.eligible }} / {{ providerState.credentials.total }} keys
            eligible</small
          >
        </article>
        <article class="metric-card">
          <span>Database</span><strong>{{ health.storage.database_bytes }} B</strong
          ><small>{{ health.storage.memory_files }} memory files</small>
        </article>
        <article class="metric-card">
          <span>Alert delivery</span><strong>{{ alertStates.pending ?? 0 }} pending</strong
          ><small>{{ alertStates.delivered ?? 0 }} delivered</small>
        </article>
      </div>
    </section>

    <section class="operations-grid" aria-label="Explicit operation checks">
      <article class="operation-card">
        <p class="eyebrow">Local and free</p>
        <h2>Database integrity</h2>
        <p>Runs SQLite <code>integrity_check</code>; it does not call a provider.</p>
        <UButton
          data-testid="check-db-integrity"
          :loading="activeCheck === 'db-integrity'"
          @click="runCheck('db-integrity')"
          >Run integrity check</UButton
        >
      </article>
      <article class="operation-card">
        <p class="eyebrow">Observational</p>
        <h2>Projection status</h2>
        <p>Checks bounded file state without creating or repairing projections.</p>
        <UButton
          :loading="activeCheck === 'projection-status'"
          @click="runCheck('projection-status')"
          >Check projections</UButton
        >
      </article>
      <article class="operation-card">
        <p class="eyebrow">Potentially paid</p>
        <h2>Provider profile</h2>
        <label
          >Profile<select v-model="providerProfile">
            <option v-for="profile in providerProfiles" :key="profile.name" :value="profile.name">
              {{ profile.name }}
            </option>
          </select></label
        ><label
          >Mode<select v-model="providerMode">
            <option value="chat">Chat</option>
            <option value="embedding">Embedding</option>
          </select></label
        ><UButton
          data-testid="check-provider-profile"
          color="warning"
          :loading="activeCheck === 'provider-profile'"
          :disabled="!providerProfile"
          @click="runProviderCheck"
          >Run provider check</UButton
        >
      </article>
      <article class="operation-card">
        <p class="eyebrow">Local model</p>
        <h2>Readiness</h2>
        <label
          >Profile<select v-model="localProfile">
            <option v-for="profile in localProfiles" :key="profile.name" :value="profile.name">
              {{ profile.name }}
            </option>
          </select></label
        ><label class="checkbox-label"
          ><input v-model="allowDownload" type="checkbox" /> Allow model load/download</label
        ><UButton
          color="warning"
          :loading="activeCheck === 'local-model-readiness'"
          :disabled="!localProfile"
          @click="runLocalCheck"
          >Run local check</UButton
        >
      </article>
    </section>

    <section
      v-if="Object.keys(results).length"
      class="observability-panel"
      aria-labelledby="check-results-title"
    >
      <div class="section-heading">
        <div>
          <p class="eyebrow">This session</p>
          <h2 id="check-results-title">Explicit check results</h2>
        </div>
      </div>
      <div class="audit-list">
        <article v-for="result in results" :key="result.check">
          <header>
            <strong>{{ result.check }} · {{ result.result }}</strong
            ><span class="health-pill" :data-state="result.ok ? 'healthy' : 'disabled'">{{
              result.ok ? "ok" : "attention"
            }}</span>
          </header>
          <p v-if="result.warning">{{ result.warning }}</p>
          <pre>{{ JSON.stringify(result.details, null, 2) }}</pre>
        </article>
      </div>
    </section>
  </ProtectedPageGate>
</template>
