<script setup lang="ts">
definePageMeta({ middleware: "auth" });

useSeoMeta({ title: "Providers · PZP AI Control Plane" });

interface ProviderProfile {
  name: string;
  provider_kind: string;
  base_url: string;
  chat_model: string;
  embedding_model: string;
  chat_capable: boolean;
  embedding_capable: boolean;
  local: boolean;
  enabled: boolean;
  source: string;
}

interface CredentialSummary {
  id: number | string;
  profile_name: string;
  label: string;
  source: string;
  environment_variable: string | null;
  last_four: string | null;
  fingerprint: string | null;
  position: number;
  state: string;
  health_state: string;
  cooldown_until: string | null;
  last_error_classification: string | null;
  success_count: number;
  failure_count: number;
  last_success_at: string | null;
  last_failure_at: string | null;
  version: number;
  revealable: boolean;
  active: boolean;
}

interface PoolSummary {
  profile_name: string;
  profile_revision: number;
  active_credential_fingerprint: string | null;
  active_credential_label: string | null;
  health_epoch: number;
  total_credentials: number;
  eligible_credentials: number;
  cooldown_credentials: number;
  disabled_credentials: number;
  last_exhausted_at: string | null;
  last_recovered_at: string | null;
}

interface CredentialTestResult {
  ok: boolean;
  mode: "chat" | "embedding";
  model: string;
  dimensions: number | null;
  final_classification: string;
  warning: string;
}

interface AdminAlert {
  id: number;
  alert_type: string;
  profile_name: string;
  credential_fingerprint: string | null;
  credential_label: string | null;
  health_epoch: number;
  recipient_telegram_id: number | null;
  state: string;
  attempts: number;
  next_attempt_at: string | null;
  created_at: string;
  delivered_at: string | null;
  error_classification: string | null;
}

interface AdminAlertPage {
  items: AdminAlert[];
  next_cursor: number | null;
  has_more: boolean;
}

const api = useControlApi();
const controlSession = useControlSession();
const profiles = ref<ProviderProfile[]>([]);
const selectedProfileName = ref("");
const credentials = ref<CredentialSummary[]>([]);
const pool = ref<PoolSummary | null>(null);
const alerts = ref<AdminAlert[]>([]);
const loading = ref(false);
const error = ref<string | null>(null);
const activeOperation = ref<string | null>(null);
const testResults = ref<Record<number, CredentialTestResult>>({});

const selectedProfile = computed(
  () => profiles.value.find((profile) => profile.name === selectedProfileName.value) ?? null,
);

onMounted(async () => {
  if (!controlSession.hasCapability("bot.admin")) {
    await navigateTo("/unauthorized");
    return;
  }
  await refreshPage();
});

async function refreshPage() {
  loading.value = true;
  error.value = null;
  try {
    profiles.value = await api.request<ProviderProfile[]>("/providers");
    if (!profiles.value.some((profile) => profile.name === selectedProfileName.value)) {
      selectedProfileName.value = profiles.value[0]?.name ?? "";
    }
    await Promise.all([loadSelectedProfile(), loadAlerts()]);
  } catch {
    error.value = "Provider operations could not be loaded.";
  } finally {
    loading.value = false;
  }
}

async function loadSelectedProfile() {
  if (!selectedProfileName.value) {
    credentials.value = [];
    pool.value = null;
    return;
  }
  const profileName = encodeURIComponent(selectedProfileName.value);
  credentials.value = await api.request<CredentialSummary[]>(
    `/providers/${profileName}/credentials`,
  );
  pool.value =
    selectedProfile.value?.source === "managed"
      ? await api.request<PoolSummary>(`/providers/${profileName}/pool`)
      : null;
}

async function loadAlerts() {
  const page = await api.request<AdminAlertPage>("/alerts?limit=25");
  alerts.value = page.items;
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
  } catch {
    error.value = "The manual provider check failed. Credential health has been refreshed.";
    await loadSelectedProfile();
  } finally {
    activeOperation.value = null;
  }
}

async function retryAlert(alertId: number) {
  activeOperation.value = `alert:${alertId}`;
  error.value = null;
  try {
    await api.request(`/alerts/${alertId}/retry`, { method: "POST" });
    await loadAlerts();
  } catch {
    error.value = "The alert could not be queued for retry.";
  } finally {
    activeOperation.value = null;
  }
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
          Inspect the active key, health transitions and sanitized delivery backlog. Manual checks
          use constant inputs and never display the credential value.
        </p>
      </div>
      <UButton color="neutral" variant="soft" :loading="loading" @click="refreshPage"
        >Refresh</UButton
      >
    </section>

    <div class="provider-warning-grid" aria-label="Manual operation warnings">
      <UAlert
        color="warning"
        variant="soft"
        title="Provider operations may incur usage charges"
        description="Chat and embedding tests run only when you explicitly select them, using a fixed non-sensitive input."
      />
      <UAlert
        color="info"
        variant="soft"
        title="Local model checks may download model files"
        description="No local readiness check runs on page load. A later explicit local check may download the configured model."
      />
    </div>

    <p v-if="error" class="operation-error" role="alert">{{ error }}</p>

    <section v-if="profiles.length" class="provider-console" aria-labelledby="pool-title">
      <div class="provider-toolbar">
        <label for="provider-profile">Provider profile</label>
        <select id="provider-profile" v-model="selectedProfileName" @change="loadSelectedProfile">
          <option v-for="profile in profiles" :key="profile.name" :value="profile.name">
            {{ profile.name }}
          </option>
        </select>
        <span v-if="selectedProfile" class="provider-models"
          >{{ selectedProfile.chat_model }} · {{ selectedProfile.embedding_model }}</span
        >
      </div>

      <p v-if="selectedProfile?.source !== 'managed'" class="empty-panel">
        Environment bootstrap profiles stay deployment-managed. Create a managed overlay to enable
        pool health and rotation.
      </p>

      <div v-if="pool" class="pool-summary-grid">
        <article>
          <span>Eligible</span
          ><strong>{{ pool.eligible_credentials }} / {{ pool.total_credentials }}</strong>
        </article>
        <article>
          <span>Active key</span><strong>{{ pool.active_credential_label ?? "None" }}</strong>
        </article>
        <article>
          <span>Cooldown</span><strong>{{ pool.cooldown_credentials }}</strong>
        </article>
        <article>
          <span>Disabled</span><strong>{{ pool.disabled_credentials }}</strong>
        </article>
        <article>
          <span>Health epoch</span><strong>{{ pool.health_epoch }}</strong>
        </article>
        <article>
          <span>Last recovery</span><strong>{{ formatTimestamp(pool.last_recovered_at) }}</strong>
        </article>
      </div>

      <div class="section-heading">
        <div>
          <p class="eyebrow">Ordered pool</p>
          <h2 id="pool-title">Credential health</h2>
        </div>
        <span>{{ credentials.length }} configured</span>
      </div>
      <div v-if="credentials.length" class="credential-list">
        <article v-for="credential in credentials" :key="credential.id" class="credential-card">
          <header>
            <div>
              <strong>{{ credential.label }}</strong
              ><small
                >{{ credential.source }} · {{ credential.fingerprint ?? "no fingerprint" }}</small
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
          <div v-if="typeof credential.id === 'number'" class="credential-actions">
            <UButton
              :data-testid="`test-chat-${credential.id}`"
              size="sm"
              color="primary"
              variant="soft"
              :loading="activeOperation === `chat:${credential.id}`"
              @click="runCredentialTest(credential, 'chat')"
              >Test chat</UButton
            >
            <UButton
              :data-testid="`test-embedding-${credential.id}`"
              size="sm"
              color="neutral"
              variant="soft"
              :loading="activeOperation === `embedding:${credential.id}`"
              @click="runCredentialTest(credential, 'embedding')"
              >Test embedding</UButton
            >
          </div>
          <p
            v-if="typeof credential.id === 'number' && testResults[credential.id]"
            class="test-result"
            aria-live="polite"
          >
            {{ testResults[Number(credential.id)]?.model }} passed ·
            {{ testResults[Number(credential.id)]?.final_classification }}
          </p>
        </article>
      </div>
      <p v-else-if="selectedProfile?.source === 'managed'" class="empty-panel">
        No credentials are configured for this managed profile.
      </p>
      <p v-else class="empty-panel">
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
          <span class="health-pill" :data-state="alert.state">{{ alert.state }}</span>
          <UButton
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
