<script setup lang="ts">
import type { components } from "~/types/openapi";

definePageMeta({ middleware: "auth" });
useSeoMeta({ title: "Query · Chatterlore Control Plane" });

type QueryCatalog = components["schemas"]["QueryCatalogResponse"];
type QueryProfile = components["schemas"]["ProviderCatalogProfile"];
type QuerySearchResponse = components["schemas"]["QuerySearchResponse"];
type QueryAskResponse = components["schemas"]["QueryAskResponse"];

const api = useControlApi();
const mode = ref<"search" | "ask">("search");
const scope = ref("");
const memoryProfile = ref("");
const queryText = ref("");
const topK = ref(6);
const catalog = ref<QueryCatalog | null>(null);
const retrievalProfileName = ref("");
const chatProfileName = ref("");
const embeddingModel = ref("");
const chatModel = ref("");
const reasoningLevel = ref("medium");
const response = ref<QuerySearchResponse | QueryAskResponse | null>(null);
const loadingCatalog = ref(true);
const running = ref(false);
const error = ref<string | null>(null);
const notice = ref<string | null>(null);
let activeController: AbortController | null = null;

const embeddingProfiles = computed(
  () =>
    catalog.value?.profiles.filter((profile) => queryLegStatus(profile, "embedding") !== null) ??
    [],
);
const chatProfiles = computed(
  () => catalog.value?.profiles.filter((profile) => queryLegStatus(profile, "chat") !== null) ?? [],
);
const selectedRetrievalProfile = computed(() => profileByName(retrievalProfileName.value));
const selectedChatProfile = computed(() => profileByName(chatProfileName.value));
const unsupportedGuidance = computed(() => {
  if (!selectedRetrievalProfile.value)
    return "Choose an available embedding profile before querying memory.";
  if (mode.value === "ask" && !selectedChatProfile.value)
    return "Ask mode also needs an available chat profile.";
  if (
    mode.value === "ask" &&
    reasoningLevel.value &&
    !selectedChatProfile.value?.capabilities.includes("reasoning")
  )
    return `${selectedChatProfile.value?.name ?? "This profile"} does not accept a reasoning-effort override.`;
  return null;
});
const askResponse = computed(() =>
  mode.value === "ask" && response.value && "answer" in response.value ? response.value : null,
);

onMounted(loadCatalog);
watch(retrievalProfileName, () => {
  const profile = selectedRetrievalProfile.value;
  if (profile) embeddingModel.value = profile.embedding_model;
});
watch(chatProfileName, () => {
  const profile = selectedChatProfile.value;
  if (profile) chatModel.value = profile.chat_model;
  if (!profile?.capabilities.includes("reasoning")) reasoningLevel.value = "";
});
watch(running, (dirty) => syncTelegramClosingConfirmation(window.Telegram?.WebApp, dirty));
onBeforeUnmount(() => {
  activeController?.abort();
  syncTelegramClosingConfirmation(window.Telegram?.WebApp, false);
});

async function loadCatalog() {
  loadingCatalog.value = true;
  error.value = null;
  try {
    if (!scope.value.trim() || scope.value.trim() === "all") {
      const resolvedScope = await resolveQueryCatalogScope(api);
      if (!resolvedScope) {
        catalog.value = null;
        error.value = "Enroll or resume an active managed chat before querying memory.";
        return;
      }
      scope.value = resolvedScope;
    }
    catalog.value = await api.request<QueryCatalog>(
      `/query/catalog?scope=${encodeURIComponent(scope.value.trim())}`,
    );
    const retrieval =
      embeddingProfiles.value.find((profile) => profile.name === retrievalProfileName.value) ??
      embeddingProfiles.value[0];
    const chat =
      chatProfiles.value.find((profile) => profile.name === chatProfileName.value) ??
      chatProfiles.value[0];
    retrievalProfileName.value = retrieval?.name ?? "";
    embeddingModel.value = retrieval?.embedding_model ?? catalog.value.embedding_models[0] ?? "";
    chatProfileName.value = chat?.name ?? "";
    chatModel.value = chat?.chat_model ?? catalog.value.chat_models[0] ?? "";
    if (!catalog.value.reasoning_levels.includes(reasoningLevel.value))
      reasoningLevel.value = catalog.value.reasoning_levels[0] ?? "";
    if (!chat?.capabilities.includes("reasoning")) reasoningLevel.value = "";
  } catch (caught) {
    error.value = controlErrorMessage(
      caught,
      "The provider and model catalog could not be loaded.",
    );
  } finally {
    loadingCatalog.value = false;
  }
}

async function runQuery() {
  const text = queryText.value.trim();
  if (!text) {
    error.value = mode.value === "ask" ? "Enter a question." : "Enter a search query.";
    return;
  }
  if (unsupportedGuidance.value) {
    error.value = unsupportedGuidance.value;
    return;
  }
  activeController?.abort();
  activeController = new AbortController();
  running.value = true;
  response.value = null;
  notice.value = null;
  error.value = null;
  const shared = {
    scope: scope.value.trim(),
    memory_profile: memoryProfile.value.trim() || null,
    retrieval_provider_profile: retrievalProfileName.value,
    chat_provider_profile: mode.value === "ask" ? chatProfileName.value : null,
    embedding_model: embeddingModel.value,
    chat_model: mode.value === "ask" ? chatModel.value : null,
    reasoning_level: mode.value === "ask" ? reasoningLevel.value || null : null,
    top_k: topK.value,
  };
  try {
    response.value =
      mode.value === "ask"
        ? await api.request<QueryAskResponse>("/query/ask", {
            method: "POST",
            body: { ...shared, question: text },
            signal: activeController.signal,
            timeout: 60_000,
          })
        : await api.request<QuerySearchResponse>("/query/search", {
            method: "POST",
            body: { ...shared, query: text },
            signal: activeController.signal,
            timeout: 60_000,
          });
  } catch (caught) {
    if (isAbortError(caught)) notice.value = "Request cancelled.";
    else
      error.value = controlErrorMessage(
        caught,
        "Query failed. Check the selected scope and provider availability.",
      );
  } finally {
    activeController = null;
    running.value = false;
  }
}

function cancelQuery() {
  activeController?.abort();
}

function profileByName(name: string): QueryProfile | null {
  return catalog.value?.profiles.find((profile) => profile.name === name) ?? null;
}

function queryLegStatus(
  profile: QueryProfile,
  leg: "chat" | "embedding",
): "ready" | "configured" | "loaded" | "not loaded" | null {
  if (!profile.enabled || !profile.capabilities.includes(leg)) return null;
  const state = profile.local_state;
  if (profile.provider_kind === "local") {
    if (!state?.[`${leg}_dependency_available`]) return null;
    return state[`${leg}_model_loaded`] ? "loaded" : "not loaded";
  }
  if (profile.provider_kind === "openai_compatible" && profile.local && state?.server_configured) {
    return "configured";
  }
  return profile.availability === "ready" ? "ready" : null;
}
</script>

<template>
  <ProtectedPageGate>
    <section class="page-introduction" aria-labelledby="query-title">
      <div>
        <p class="eyebrow">Source-backed workspace</p>
        <h1 id="query-title">Ask the memory, on your terms.</h1>
        <p class="page-lede">
          Select the destination scope and each provider leg explicitly. Answers show sources and
          selection metadata, never hidden reasoning.
        </p>
      </div>
      <UBadge color="neutral" variant="soft">{{
        mode === "ask" ? "Chat + retrieval" : "Retrieval only"
      }}</UBadge>
    </section>

    <p v-if="loadingCatalog" class="empty-panel" role="status">
      Loading approved profiles and models…
    </p>
    <p v-if="error" class="operation-error" role="alert">{{ error }}</p>
    <p v-if="notice" class="operation-notice" aria-live="polite">{{ notice }}</p>

    <form
      v-if="catalog"
      class="query-workspace"
      aria-label="Memory query controls"
      @submit.prevent="runQuery"
    >
      <div class="query-mode-row">
        <label
          >Mode<select v-model="mode" data-testid="query-mode">
            <option value="search">Search</option>
            <option value="ask">Ask</option>
          </select></label
        >
        <label>Scope<input v-model="scope" autocomplete="off" @change="loadCatalog" /></label>
        <label
          >Memory profile<input
            v-model="memoryProfile"
            placeholder="Effective default"
            autocomplete="off"
        /></label>
        <label>Top K<input v-model.number="topK" type="number" min="1" max="20" /></label>
      </div>

      <div class="query-provider-grid">
        <fieldset>
          <legend>Retrieval leg</legend>
          <label
            >Profile<select v-model="retrievalProfileName" data-testid="retrieval-profile">
              <option
                v-for="profile in embeddingProfiles"
                :key="profile.name"
                :value="profile.name"
              >
                {{ profile.name }} · {{ queryLegStatus(profile, "embedding") }}
              </option>
            </select></label
          >
          <label
            >Embedding model<select v-model="embeddingModel">
              <option v-for="model in catalog.embedding_models" :key="model" :value="model">
                {{ model }}
              </option>
            </select></label
          >
          <small
            v-if="
              selectedRetrievalProfile?.provider_kind === 'local' &&
              selectedRetrievalProfile.local_state
            "
            >Embedding dependency
            {{
              selectedRetrievalProfile.local_state.embedding_dependency_available
                ? "available"
                : "missing"
            }}
            · model
            {{
              selectedRetrievalProfile.local_state.embedding_model_loaded ? "loaded" : "not loaded"
            }}</small
          >
          <small v-else-if="selectedRetrievalProfile?.local_state?.server_configured"
            >Local server configured. Reachability is checked when you run a query.</small
          >
        </fieldset>
        <fieldset>
          <legend>Chat leg</legend>
          <label
            >Profile<select v-model="chatProfileName" data-testid="chat-profile">
              <option v-for="profile in chatProfiles" :key="profile.name" :value="profile.name">
                {{ profile.name }} · {{ queryLegStatus(profile, "chat") }}
              </option>
            </select></label
          >
          <label
            >Chat model<select v-model="chatModel">
              <option v-for="model in catalog.chat_models" :key="model" :value="model">
                {{ model }}
              </option>
            </select></label
          >
          <label
            >Reasoning effort<select
              v-model="reasoningLevel"
              :disabled="!selectedChatProfile?.capabilities.includes('reasoning')"
            >
              <option value="">Provider default (no override)</option>
              <option v-for="level in catalog.reasoning_levels" :key="level" :value="level">
                {{ level }}
              </option>
            </select></label
          >
        </fieldset>
      </div>

      <p v-if="unsupportedGuidance" class="empty-panel">{{ unsupportedGuidance }}</p>
      <label class="query-input-label"
        >{{ mode === "ask" ? "Question" : "Search query"
        }}<textarea v-model="queryText" data-testid="query-input" rows="5" maxlength="8000" />
      </label>
      <div class="state-actions">
        <UButton data-testid="run-query" type="button" :loading="running" @click="runQuery">{{
          mode === "ask" ? "Ask memory" : "Search memory"
        }}</UButton>
        <UButton
          v-if="running"
          data-testid="cancel-query"
          type="button"
          color="neutral"
          variant="soft"
          @click="cancelQuery"
          >Cancel</UButton
        >
      </div>
    </form>

    <section
      v-if="response"
      class="query-results"
      aria-labelledby="query-results-title"
      aria-live="polite"
    >
      <div class="section-heading">
        <div>
          <p class="eyebrow">{{ response.elapsed_ms }} ms · {{ response.destination_scope }}</p>
          <h2 id="query-results-title">
            {{ mode === "ask" ? "Answer and evidence" : "Search results" }}
          </h2>
        </div>
        <span
          >{{ response.results.length }} source{{ response.results.length === 1 ? "" : "s" }}</span
        >
      </div>
      <article v-if="askResponse" class="answer-card">
        <strong>{{ askResponse.insufficient_context ? "Insufficient context" : "Answer" }}</strong>
        <p>{{ askResponse.answer }}</p>
      </article>
      <div v-if="response.results.length" class="result-list">
        <article v-for="(item, index) in response.results" :key="`${item.scope}-${index}`">
          <header>
            <strong>{{ item.source }}</strong
            ><span>{{ Math.round(item.score * 100) }}% · {{ item.scope }}</span>
          </header>
          <p>{{ item.content }}</p>
          <small v-if="item.source_message_ids.length"
            >Messages {{ item.source_message_ids.join(", ") }}</small
          >
        </article>
      </div>
      <p v-else class="empty-panel">No memory matched this query.</p>
    </section>
  </ProtectedPageGate>
</template>
