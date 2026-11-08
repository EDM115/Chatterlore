<script setup lang="ts">
import type { components } from "~/types/openapi";

definePageMeta({ middleware: "auth" });
useSeoMeta({ title: "Stats · Chatterlore Control Plane" });

type StatsOverview = components["schemas"]["StatsOverviewResponse"];
type StatsTimeseries = components["schemas"]["StatsTimeseriesResponse"];

const api = useControlApi();
const controlSession = useControlSession();
const scope = ref(
  controlSession.hasCapability("bot.admin")
    ? "all"
    : (Object.keys(controlSession.session.value?.chat_capabilities ?? {})[0] ?? "all"),
);
const days = ref(30);
const overview = ref<StatsOverview | null>(null);
const timeseries = ref<StatsTimeseries | null>(null);
const loading = ref(true);
const error = ref<string | null>(null);

onMounted(loadStats);

async function loadStats() {
  loading.value = true;
  error.value = null;
  try {
    [overview.value, timeseries.value] = await Promise.all([
      api.request<StatsOverview>(`/stats/overview?scope=${encodeURIComponent(scope.value)}`),
      api.request<StatsTimeseries>(
        `/stats/timeseries?scope=${encodeURIComponent(scope.value)}&days=${days.value}`,
      ),
    ]);
  } catch (caught) {
    error.value = controlErrorMessage(caught, "Statistics could not be loaded for this scope.");
  } finally {
    loading.value = false;
  }
}

function chatStates(states: Record<string, number>): string {
  return Object.entries(states)
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([state, count]) => `${count} ${state}`)
    .join(" · ");
}
</script>

<template>
  <ProtectedPageGate>
    <section class="page-introduction" aria-labelledby="stats-title">
      <div>
        <p class="eyebrow">Bounded observability</p>
        <h1 id="stats-title">See activity without scanning history.</h1>
        <p class="page-lede">
          SQL-backed hierarchy counts and a bounded daily series make global, chat and topic
          behavior comparable without provider calls or local-model loads.
        </p>
      </div>
      <UButton color="neutral" variant="soft" :loading="loading" @click="loadStats"
        >Refresh</UButton
      >
    </section>
    <form class="filter-bar" @submit.prevent="loadStats">
      <label>Scope<input v-model="scope" /></label
      ><label
        >Trend window<select v-model.number="days">
          <option :value="7">7 days</option>
          <option :value="30">30 days</option>
          <option :value="90">90 days</option>
          <option :value="366">366 days</option>
        </select></label
      ><UButton type="submit">Apply</UButton>
    </form>
    <p v-if="loading && !overview" class="empty-panel" role="status">
      Loading aggregated statistics…
    </p>
    <p v-if="error" class="operation-error" role="alert">{{ error }}</p>

    <template v-if="overview">
      <section class="metric-grid" aria-label="Detailed statistics">
        <article class="metric-card">
          <span>Messages</span><strong>{{ overview.memory.messages }}</strong
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
          <span>AI usage</span><strong>{{ overview.ai_usage.total }}</strong
          ><small
            >{{ overview.ai_usage.errors }} errors ·
            {{ overview.ai_usage.average_latency_ms ?? "—" }} ms</small
          >
        </article>
        <article class="metric-card">
          <span>Provider profiles</span><strong>{{ overview.providers.profiles }}</strong
          ><small
            >{{ overview.providers.credentials.eligible }} /
            {{ overview.providers.credentials.total }} keys eligible</small
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
          ><small>{{ chatStates(overview.administration.managed_chats) }}</small>
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
          <span>Local models</span><strong>{{ overview.providers.local_models_loaded }}</strong
          ><small
            >Dependency
            {{ overview.providers.local_dependency_available ? "available" : "missing" }}</small
          >
        </article>
        <article class="metric-card">
          <span>Runtime</span><strong>Schema {{ overview.runtime.schema_revision }}</strong
          ><small>{{ overview.runtime.uptime_seconds }} seconds uptime</small>
        </article>
      </section>
      <section class="observability-panel" aria-labelledby="scheduler-title">
        <div class="section-heading">
          <div>
            <p class="eyebrow">Runtime supervision</p>
            <h2 id="scheduler-title">Scheduler tasks</h2>
          </div>
          <span>{{ overview.runtime.scheduler.length }} known</span>
        </div>
        <div class="status-list">
          <article v-for="task in overview.runtime.scheduler" :key="task.name">
            <div>
              <strong>{{ task.name }}</strong
              ><small
                >{{ task.state }} · {{ task.consecutive_failures }} consecutive failures</small
              >
            </div>
            <span class="health-pill" :data-state="task.healthy ? 'healthy' : 'disabled'">{{
              task.healthy ? "healthy" : "attention"
            }}</span>
          </article>
        </div>
      </section>
    </template>

    <section v-if="timeseries" class="observability-panel" aria-labelledby="timeseries-title">
      <div class="section-heading">
        <div>
          <p class="eyebrow">{{ timeseries.days }} days</p>
          <h2 id="timeseries-title">Daily hierarchy trend</h2>
        </div>
        <span>{{ timeseries.scope }}</span>
      </div>
      <div v-if="timeseries.points.length" class="trend-list">
        <article v-for="point in timeseries.points" :key="point.date">
          <time :datetime="point.date">{{ point.date }}</time
          ><strong>{{ point.messages }} messages</strong
          ><span
            >{{ point.learned }} learned · {{ point.skipped }} skipped ·
            {{ point.knowledge_items }} knowledge · {{ point.ai_calls }} AI /
            {{ point.ai_errors }} errors</span
          >
        </article>
      </div>
      <p v-else class="empty-panel">No activity was recorded in this window.</p>
    </section>
  </ProtectedPageGate>
</template>
