<script setup lang="ts">
import type { components } from "../types/openapi";

definePageMeta({ middleware: "auth" });

useSeoMeta({ title: "Dashboard · PZP AI Control Plane" });

type StatsOverview = components["schemas"]["StatsOverviewResponse"];
type StatsTimeseries = components["schemas"]["StatsTimeseriesResponse"];
type OperationsHealth = components["schemas"]["OperationsHealthResponse"];

const api = useControlApi();
const controlSession = useControlSession();
const overview = ref<StatsOverview | null>(null);
const timeseries = ref<StatsTimeseries | null>(null);
const health = ref<OperationsHealth | null>(null);
const loading = ref(true);
const error = ref<string | null>(null);

const scope = computed(() => {
  if (controlSession.hasCapability("bot.admin")) return "all";
  return Object.keys(controlSession.session.value?.chat_capabilities ?? {})[0] ?? "all";
});

const managedChatSummary = computed(() => {
  const states = overview.value?.administration.managed_chats ?? {};
  return Object.entries(states)
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([state, count]) => `${count} ${state}`)
    .join(" · ");
});

const pendingAlerts = computed(() => overview.value?.alerts.states.pending ?? 0);

onMounted(loadDashboard);

async function loadDashboard() {
  loading.value = true;
  error.value = null;
  const queryScope = encodeURIComponent(scope.value);
  try {
    const [nextOverview, nextTimeseries, nextHealth] = await Promise.all([
      api.request<StatsOverview>(`/stats/overview?scope=${queryScope}`),
      api.request<StatsTimeseries>(`/stats/timeseries?scope=${queryScope}&days=14`),
      controlSession.hasCapability("bot.admin")
        ? api.request<OperationsHealth>("/operations/health")
        : Promise.resolve(null),
    ]);
    overview.value = nextOverview;
    timeseries.value = nextTimeseries;
    health.value = nextHealth;
  } catch (caught) {
    error.value = controlErrorMessage(
      caught,
      "Dashboard status could not be loaded. Your session is still protected; retry when the backend is available.",
    );
  } finally {
    loading.value = false;
  }
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KiB", "MiB", "GiB"];
  let value = bytes / 1024;
  let unit = units[0];
  for (let index = 1; index < units.length && value >= 1024; index += 1) {
    value /= 1024;
    unit = units[index];
  }
  return `${value.toFixed(value >= 10 ? 0 : 1)} ${unit}`;
}

function formatDuration(seconds: number): string {
  const days = Math.floor(seconds / 86_400);
  const hours = Math.floor((seconds % 86_400) / 3_600);
  const minutes = Math.floor((seconds % 3_600) / 60);
  return [days ? `${days}d` : "", hours ? `${hours}h` : "", `${minutes}m`]
    .filter(Boolean)
    .join(" ");
}
</script>

<template>
  <ProtectedPageGate>
    <section class="page-introduction" aria-labelledby="dashboard-title">
      <div>
        <p class="eyebrow">Live control plane · {{ scope }}</p>
        <h1 id="dashboard-title">The bot at a glance.</h1>
        <p class="page-lede">
          One bounded snapshot of memory, activity, storage, providers and administration. Opening
          this page never calls a paid provider or loads a local model.
        </p>
      </div>
      <UButton color="neutral" variant="soft" :loading="loading" @click="loadDashboard"
        >Refresh</UButton
      >
    </section>

    <p v-if="loading && !overview" class="empty-panel" role="status" aria-live="polite">
      Loading the bounded status snapshot…
    </p>
    <p v-if="error" class="operation-error" role="alert">{{ error }}</p>

    <section v-if="overview" class="metric-grid" aria-label="Current bot metrics">
      <article class="metric-card">
        <span>Messages ingested</span><strong>{{ overview.memory.messages }}</strong
        ><small
          >{{ overview.memory.learned }} learned · {{ overview.memory.skipped }} skipped</small
        >
      </article>
      <article class="metric-card">
        <span>Knowledge</span><strong>{{ overview.memory.knowledge_items }}</strong
        ><small
          >{{ overview.memory.embeddings }} embeddings ·
          {{ overview.memory.pending_reviews }} reviews</small
        >
      </article>
      <article class="metric-card">
        <span>Managed chats</span
        ><strong>{{
          Object.values(overview.administration.managed_chats).reduce(
            (total, count) => total + count,
            0,
          )
        }}</strong
        ><small>{{ managedChatSummary || "No managed chats" }}</small>
      </article>
      <article class="metric-card">
        <span>Administrators</span
        ><strong>{{
          overview.administration.bot_admins + overview.administration.delegated_chat_admins
        }}</strong
        ><small
          >{{ overview.administration.bot_admins }} bot ·
          {{ overview.administration.delegated_chat_admins }} delegated</small
        >
      </article>
      <article class="metric-card">
        <span>AI operations</span><strong>{{ overview.ai_usage.total }}</strong
        ><small
          >{{ overview.ai_usage.errors }} errors ·
          {{ overview.ai_usage.average_latency_ms ?? "—" }} ms average</small
        >
      </article>
      <article class="metric-card">
        <span>Provider keys</span
        ><strong
          >{{ overview.providers.credentials.eligible }} /
          {{ overview.providers.credentials.total }}</strong
        ><small
          >{{ overview.providers.profiles }} profiles ·
          {{ overview.providers.local_models_loaded }} local loaded</small
        >
      </article>
      <article class="metric-card">
        <span>Database</span><strong>{{ formatBytes(overview.storage.database_bytes) }}</strong
        ><small
          >{{ formatBytes(overview.storage.memory_bytes) }} projections ·
          {{ overview.storage.memory_files }} files</small
        >
      </article>
      <article class="metric-card">
        <span>Uptime</span><strong>{{ formatDuration(overview.runtime.uptime_seconds) }}</strong
        ><small>Schema {{ overview.runtime.schema_revision }}</small>
      </article>
    </section>

    <section
      v-if="health"
      class="next-step-panel"
      aria-labelledby="health-title"
      data-testid="dashboard-health"
    >
      <div>
        <p class="eyebrow">Cached status · No provider call</p>
        <h2 id="health-title">Operational snapshot</h2>
        <p>
          {{ health.checks.length }} explicit diagnostics are available in Operations.
          {{ pendingAlerts }} pending alert{{ pendingAlerts === 1 ? "" : "s" }}; manual paid or
          download-triggering checks run only after a separate action.
        </p>
      </div>
      <UButton to="/operations" color="primary" variant="solid">Open Operations</UButton>
    </section>

    <section
      v-if="timeseries?.points.length"
      class="trend-panel"
      aria-labelledby="dashboard-trend-title"
    >
      <div class="section-heading">
        <div>
          <p class="eyebrow">14-day activity</p>
          <h2 id="dashboard-trend-title">Lightweight trends</h2>
        </div>
        <UButton to="/stats" color="neutral" variant="ghost">Detailed stats</UButton>
      </div>
      <div class="trend-list">
        <article v-for="point in timeseries.points" :key="point.date">
          <time :datetime="point.date">{{ point.date }}</time
          ><strong>{{ point.messages }} messages</strong
          ><span
            >{{ point.knowledge_items }} knowledge · {{ point.ai_calls }} AI ·
            {{ point.ai_errors }} errors</span
          >
        </article>
      </div>
    </section>
  </ProtectedPageGate>
</template>
