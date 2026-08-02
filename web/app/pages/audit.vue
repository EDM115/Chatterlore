<script setup lang="ts">
import type { components } from "../types/openapi";

definePageMeta({ middleware: "auth" });
useSeoMeta({ title: "Audit · PZP AI Control Plane" });

type AuditEvent = components["schemas"]["AuditEventResponse"];
type AuditEventPage = components["schemas"]["AuditEventPage"];
type AIUsage = components["schemas"]["AIUsageResponse"];
type AIUsagePage = components["schemas"]["AIUsagePage"];

const api = useControlApi();
const controlSession = useControlSession();
const scope = ref(
  controlSession.hasCapability("bot.admin")
    ? "all"
    : (Object.keys(controlSession.session.value?.chat_capabilities ?? {})[0] ?? "all"),
);
const eventType = ref("");
const events = ref<AuditEvent[]>([]);
const usage = ref<AIUsage[]>([]);
const eventCursor = ref<number | null>(null);
const usageCursor = ref<number | null>(null);
const hasMoreEvents = ref(false);
const hasMoreUsage = ref(false);
const loading = ref(true);
const error = ref<string | null>(null);

onMounted(refreshAudit);

async function refreshAudit() {
  loading.value = true;
  error.value = null;
  eventCursor.value = null;
  usageCursor.value = null;
  try {
    await Promise.all([loadEvents(false), loadUsage(false)]);
  } catch (caught) {
    error.value = controlErrorMessage(caught, "Audit and AI usage records could not be loaded.");
  } finally {
    loading.value = false;
  }
}

async function loadEvents(append: boolean) {
  const cursor = append && eventCursor.value ? `&cursor=${eventCursor.value}` : "";
  const type = eventType.value.trim()
    ? `&event_type=${encodeURIComponent(eventType.value.trim())}`
    : "";
  const page = await api.request<AuditEventPage>(
    `/audit/events?scope=${encodeURIComponent(scope.value)}&limit=25${type}${cursor}`,
  );
  events.value = append ? [...events.value, ...page.items] : page.items;
  eventCursor.value = page.next_cursor;
  hasMoreEvents.value = page.has_more;
}

async function loadUsage(append: boolean) {
  const cursor = append && usageCursor.value ? `&cursor=${usageCursor.value}` : "";
  const page = await api.request<AIUsagePage>(
    `/audit/ai-usage?scope=${encodeURIComponent(scope.value)}&limit=25${cursor}`,
  );
  usage.value = append ? [...usage.value, ...page.items] : page.items;
  usageCursor.value = page.next_cursor;
  hasMoreUsage.value = page.has_more;
}

function timestamp(value: string): string {
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(
    new Date(value),
  );
}
</script>

<template>
  <ProtectedPageGate>
    <section class="page-introduction" aria-labelledby="audit-title">
      <div>
        <p class="eyebrow">Explainable operations</p>
        <h1 id="audit-title">Changes leave a safe trail.</h1>
        <p class="page-lede">
          Inspect bounded, scope-authorized event and AI usage records. Payload projection removes
          secret-like fields, prompts and provider bodies at the backend boundary.
        </p>
      </div>
      <UButton color="neutral" variant="soft" :loading="loading" @click="refreshAudit"
        >Refresh</UButton
      >
    </section>
    <form class="filter-bar" @submit.prevent="refreshAudit">
      <label>Scope<input v-model="scope" /></label
      ><label>Event type<input v-model="eventType" placeholder="All event types" /></label
      ><UButton type="submit">Apply filters</UButton>
    </form>
    <p v-if="loading && !events.length && !usage.length" class="empty-panel" role="status">
      Loading bounded audit pages…
    </p>
    <p v-if="error" class="operation-error" role="alert">{{ error }}</p>
    <section class="observability-panel" aria-labelledby="events-title">
      <div class="section-heading">
        <div>
          <p class="eyebrow">Audit events</p>
          <h2 id="events-title">Administrative and memory changes</h2>
        </div>
        <span>{{ events.length }} loaded</span>
      </div>
      <p v-if="!loading && !events.length" class="empty-panel">No matching audit events.</p>
      <div class="audit-list">
        <article v-for="event in events" :key="event.id">
          <header>
            <div>
              <strong>{{ event.event_type }}</strong
              ><small
                >#{{ event.id }} · {{ event.scope ?? "global" }} · actor
                {{ event.user_id ?? "system" }} · {{ timestamp(event.created_at) }}</small
              >
            </div>
          </header>
          <details>
            <summary>Safe event detail</summary>
            <pre>{{ JSON.stringify(event.payload, null, 2) }}</pre>
          </details>
        </article>
      </div>
      <UButton v-if="hasMoreEvents" color="neutral" variant="soft" @click="loadEvents(true)"
        >Load more events</UButton
      >
    </section>
    <section class="observability-panel" aria-labelledby="usage-title">
      <div class="section-heading">
        <div>
          <p class="eyebrow">AI usage</p>
          <h2 id="usage-title">Provider operations</h2>
        </div>
        <span>{{ usage.length }} loaded</span>
      </div>
      <p v-if="!loading && !usage.length" class="empty-panel">No matching AI usage records.</p>
      <div class="status-list">
        <article v-for="item in usage" :key="item.id">
          <div>
            <strong>{{ item.operation }} · {{ item.model }}</strong
            ><small
              >{{ item.provider }} · {{ item.scope ?? "global" }} ·
              {{ item.prompt_tokens ?? "—" }} prompt /
              {{ item.completion_tokens ?? "—" }} completion · {{ item.latency_ms ?? "—" }} ms ·
              {{ timestamp(item.created_at) }}</small
            >
          </div>
          <span class="health-pill" :data-state="item.error ? 'disabled' : 'healthy'">{{
            item.error ?? "ok"
          }}</span>
        </article>
      </div>
      <UButton v-if="hasMoreUsage" color="neutral" variant="soft" @click="loadUsage(true)"
        >Load more usage</UButton
      >
    </section>
  </ProtectedPageGate>
</template>
