<script setup lang="ts">
import type { components } from "../types/openapi";

definePageMeta({ middleware: "auth" });
useSeoMeta({ title: "Memory · PZP AI Control Plane" });

type MemoryItem = components["schemas"]["MemoryItemResponse"];
type MemoryItemPage = components["schemas"]["MemoryItemPage"];
type MemoryRawSources = components["schemas"]["MemoryRawSourcesResponse"];
type MemoryItemRefresh = components["schemas"]["MemoryItemRefreshResponse"];
type MemoryReview = components["schemas"]["MemoryReviewResponse"];
type MemoryReviewPage = components["schemas"]["MemoryReviewPage"];

const api = useControlApi();
const config = useRuntimeConfig();
const controlSession = useControlSession();
const scope = ref(
  controlSession.hasCapability("bot.admin")
    ? "all"
    : (Object.keys(controlSession.session.value?.chat_capabilities ?? {})[0] ?? "all"),
);
const kind = ref("");
const items = ref<MemoryItem[]>([]);
const reviews = ref<MemoryReview[]>([]);
const itemCursor = ref(0);
const reviewCursor = ref(0);
const nextItemCursor = ref<number | null>(null);
const nextReviewCursor = ref<number | null>(null);
const hasMoreItems = ref(false);
const hasMoreReviews = ref(false);
const rawSources = ref<Record<number, MemoryRawSources>>({});
const refreshModes = ref<Record<number, "embed" | "summarize" | "both">>({});
const refreshResults = ref<Record<number, MemoryItemRefresh>>({});
const pendingDeleteId = ref<number | null>(null);
const loading = ref(true);
const activeOperation = ref<string | null>(null);
const error = ref<string | null>(null);
const notice = ref<string | null>(null);

const exportBase = computed(
  () => `${config.public.controlApiBase}/memory/export?scope=${encodeURIComponent(scope.value)}`,
);

onMounted(refreshExplorer);

async function refreshExplorer() {
  itemCursor.value = 0;
  reviewCursor.value = 0;
  loading.value = true;
  error.value = null;
  try {
    await Promise.all([loadItems(false), loadReviews(false)]);
  } catch (caught) {
    error.value = controlErrorMessage(caught, "Memory and review queues could not be loaded.");
  } finally {
    loading.value = false;
  }
}

async function loadItems(append: boolean) {
  const cursor = append ? (nextItemCursor.value ?? 0) : itemCursor.value;
  const kindQuery = kind.value.trim() ? `&kind=${encodeURIComponent(kind.value.trim())}` : "";
  const page = await api.request<MemoryItemPage>(
    `/memory/items?scope=${encodeURIComponent(scope.value)}&limit=25&cursor=${cursor}${kindQuery}`,
  );
  items.value = append ? [...items.value, ...page.items] : page.items;
  nextItemCursor.value = page.next_cursor;
  hasMoreItems.value = page.has_more;
}

async function loadReviews(append: boolean) {
  const cursor = append ? (nextReviewCursor.value ?? 0) : reviewCursor.value;
  const page = await api.request<MemoryReviewPage>(
    `/memory/reviews?scope=${encodeURIComponent(scope.value)}&limit=25&cursor=${cursor}`,
  );
  reviews.value = append ? [...reviews.value, ...page.items] : page.items;
  nextReviewCursor.value = page.next_cursor;
  hasMoreReviews.value = page.has_more;
}

async function showRawSources(item: MemoryItem) {
  activeOperation.value = `sources:${item.id}`;
  error.value = null;
  try {
    rawSources.value = {
      ...rawSources.value,
      [item.id]: await api.request<MemoryRawSources>(`/memory/items/${item.id}/sources`, {
        method: "POST",
      }),
    };
  } catch (caught) {
    error.value = controlErrorMessage(caught, "Raw Telegram source access failed.");
  } finally {
    activeOperation.value = null;
  }
}

async function refreshItem(item: MemoryItem) {
  const mode = refreshModes.value[item.id] ?? "embed";
  activeOperation.value = `refresh:${item.id}`;
  error.value = null;
  try {
    const result = await api.request<MemoryItemRefresh>(`/memory/items/${item.id}/refresh`, {
      method: "POST",
      body: { mode },
    });
    refreshResults.value = { ...refreshResults.value, [item.id]: result };
    notice.value = `Memory item ${item.id} refreshed.`;
  } catch (caught) {
    error.value = controlErrorMessage(caught, "The memory item could not be refreshed.");
  } finally {
    activeOperation.value = null;
  }
}

async function deleteItem(item: MemoryItem) {
  activeOperation.value = `delete:${item.id}`;
  error.value = null;
  try {
    await api.request(`/memory/items/${item.id}`, { method: "DELETE" });
    pendingDeleteId.value = null;
    rawSources.value = Object.fromEntries(
      Object.entries(rawSources.value).filter(([id]) => Number(id) !== item.id),
    );
    await loadItems(false);
    notice.value = `Memory item ${item.id} deleted.`;
  } catch (caught) {
    error.value = controlErrorMessage(caught, "The memory item could not be deleted.");
  } finally {
    activeOperation.value = null;
  }
}

async function reviewAction(review: MemoryReview, action: "approve" | "reject") {
  activeOperation.value = `${action}:${review.id}`;
  error.value = null;
  try {
    await api.request(`/memory/reviews/${review.id}/${action}`, { method: "POST" });
    reviews.value = reviews.value.filter((candidate) => candidate.id !== review.id);
    notice.value = `Review ${review.id} ${action === "approve" ? "approved" : "rejected"}.`;
  } catch (caught) {
    error.value = controlErrorMessage(caught, `The review could not be ${action}d.`);
  } finally {
    activeOperation.value = null;
  }
}

function formatTimestamp(value: string): string {
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(
    new Date(value),
  );
}
</script>

<template>
  <ProtectedPageGate>
    <section class="page-introduction" aria-labelledby="memory-title">
      <div>
        <p class="eyebrow">Durable knowledge</p>
        <h1 id="memory-title">Inspect what the bot remembers.</h1>
        <p class="page-lede">
          Browse bounded pages, review compacted candidates, explicitly reveal raw Telegram sources
          and stream exports without loading the complete memory into the browser.
        </p>
      </div>
      <div class="state-actions">
        <a :href="`${exportBase}&format=md`" data-testid="export-md" class="download-link"
          >Export Markdown</a
        >
        <a :href="`${exportBase}&format=jsonl`" data-testid="export-jsonl" class="download-link"
          >Export JSONL</a
        >
      </div>
    </section>

    <form class="filter-bar" @submit.prevent="refreshExplorer">
      <label>Scope<input v-model="scope" autocomplete="off" /></label>
      <label>Kind<input v-model="kind" placeholder="All kinds" autocomplete="off" /></label>
      <UButton type="submit" :loading="loading">Apply filters</UButton>
    </form>
    <p v-if="loading && !items.length && !reviews.length" class="empty-panel" role="status">
      Loading memory and review queues…
    </p>
    <p v-if="error" class="operation-error" role="alert">{{ error }}</p>
    <p v-if="notice" class="operation-notice" aria-live="polite">{{ notice }}</p>

    <section class="explorer-panel" aria-labelledby="memory-items-title">
      <div class="section-heading">
        <div>
          <p class="eyebrow">Knowledge items</p>
          <h2 id="memory-items-title">Current memory</h2>
        </div>
        <span>{{ items.length }} loaded</span>
      </div>
      <p v-if="!loading && !items.length" class="empty-panel">
        No knowledge items match this scope and kind.
      </p>
      <div class="explorer-list">
        <article v-for="item in items" :key="item.id" class="explorer-card">
          <header>
            <div>
              <strong>{{ item.kind }} · #{{ item.id }}</strong
              ><small>{{ item.scope }} · updated {{ formatTimestamp(item.updated_at) }}</small>
            </div>
            <UBadge color="neutral" variant="soft"
              >{{ item.source_message_ids.length }} source{{
                item.source_message_ids.length === 1 ? "" : "s"
              }}</UBadge
            >
          </header>
          <p>{{ item.content }}</p>
          <details v-if="Object.keys(item.metadata).length">
            <summary>Safe metadata</summary>
            <pre>{{ JSON.stringify(item.metadata, null, 2) }}</pre>
          </details>
          <div class="credential-actions">
            <UButton
              :data-testid="`raw-sources-${item.id}`"
              size="sm"
              color="warning"
              variant="soft"
              :loading="activeOperation === `sources:${item.id}`"
              @click="showRawSources(item)"
              >Show raw sources</UButton
            >
            <select
              v-model="refreshModes[item.id]"
              :data-testid="`refresh-mode-${item.id}`"
              :aria-label="`Refresh mode for item ${item.id}`"
            >
              <option value="embed">Embedding</option>
              <option value="summarize">Summary</option>
              <option value="both">Both</option>
            </select>
            <UButton
              :data-testid="`refresh-item-${item.id}`"
              size="sm"
              color="neutral"
              variant="soft"
              :loading="activeOperation === `refresh:${item.id}`"
              @click="refreshItem(item)"
              >Refresh</UButton
            >
            <UButton
              :data-testid="`delete-item-${item.id}`"
              size="sm"
              color="error"
              variant="soft"
              @click="pendingDeleteId = item.id"
              >Delete</UButton
            >
          </div>
          <p v-if="refreshResults[item.id]" class="operation-notice">
            {{ refreshResults[item.id]?.embeddings_refreshed }} embedding{{
              refreshResults[item.id]?.embeddings_refreshed === 1 ? "" : "s"
            }}
            refreshed · summarized {{ refreshResults[item.id]?.summarized ? "yes" : "no" }}
          </p>
          <section
            v-if="rawSources[item.id]"
            class="raw-source-panel"
            :aria-label="`Raw sources for item ${item.id}`"
          >
            <p><strong>Raw Telegram content</strong> · explicitly requested and audited.</p>
            <article
              v-for="source in rawSources[item.id]?.sources"
              :key="source.telegram_message_id"
            >
              <header>
                <span>{{
                  source.username ? `@${source.username}` : (source.user_id ?? "Unknown sender")
                }}</span
                ><a
                  v-if="source.message_permalink"
                  :href="source.message_permalink"
                  target="_blank"
                  rel="noreferrer"
                  >Message {{ source.telegram_message_id }}</a
                >
              </header>
              <p>{{ source.text }}</p>
            </article>
          </section>
          <div
            v-if="pendingDeleteId === item.id"
            class="confirmation-panel"
            role="alertdialog"
            :aria-labelledby="`delete-title-${item.id}`"
          >
            <strong :id="`delete-title-${item.id}`">Delete item {{ item.id }} permanently?</strong
            ><span>This removes the durable item; it does not erase unrelated chat history.</span>
            <div class="state-actions">
              <UButton
                :data-testid="`confirm-delete-item-${item.id}`"
                color="error"
                :loading="activeOperation === `delete:${item.id}`"
                @click="deleteItem(item)"
                >Confirm delete</UButton
              ><UButton color="neutral" variant="ghost" @click="pendingDeleteId = null"
                >Cancel</UButton
              >
            </div>
          </div>
        </article>
      </div>
      <UButton v-if="hasMoreItems" color="neutral" variant="soft" @click="loadItems(true)"
        >Load more items</UButton
      >
    </section>

    <section class="explorer-panel" aria-labelledby="memory-reviews-title">
      <div class="section-heading">
        <div>
          <p class="eyebrow">Review queue</p>
          <h2 id="memory-reviews-title">Pending compaction reviews</h2>
        </div>
        <span>{{ reviews.length }} loaded</span>
      </div>
      <p v-if="!loading && !reviews.length" class="empty-panel">
        No reviews are waiting in this scope.
      </p>
      <div class="explorer-list">
        <article v-for="review in reviews" :key="review.id" class="explorer-card">
          <header>
            <div>
              <strong>{{ review.kind }} · review #{{ review.id }}</strong
              ><small>{{ review.scope }} · {{ formatTimestamp(review.created_at) }}</small>
            </div>
          </header>
          <p>{{ review.content }}</p>
          <div class="credential-actions">
            <UButton
              :data-testid="`approve-review-${review.id}`"
              size="sm"
              :loading="activeOperation === `approve:${review.id}`"
              @click="reviewAction(review, 'approve')"
              >Approve</UButton
            ><UButton
              :data-testid="`reject-review-${review.id}`"
              size="sm"
              color="error"
              variant="soft"
              :loading="activeOperation === `reject:${review.id}`"
              @click="reviewAction(review, 'reject')"
              >Reject</UButton
            >
          </div>
        </article>
      </div>
      <UButton v-if="hasMoreReviews" color="neutral" variant="soft" @click="loadReviews(true)"
        >Load more reviews</UButton
      >
    </section>
  </ProtectedPageGate>
</template>
