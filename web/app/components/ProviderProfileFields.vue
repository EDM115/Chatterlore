<script setup lang="ts">
import type { ProviderForm } from "~/types/providerForms";

defineProps<{ prefix: string }>();
const draft = defineModel<ProviderForm>({ required: true });

watch(
  () => draft.value.provider_kind,
  (kind) => {
    if (kind === "local") {
      draft.value.local = true;
      draft.value.auth_mode = "none";
      draft.value.api_dialect = "generic";
      draft.value.reasoning_capable = false;
    }
  },
);
watch(
  () => draft.value.local,
  (local) => {
    if (!local && draft.value.provider_kind === "openai_compatible")
      draft.value.auth_mode = "api_key";
  },
);
</script>

<template>
  <label
    >Backend<select v-model="draft.provider_kind" :data-testid="`${prefix}-kind`">
      <option value="openai_compatible">OpenAI-compatible HTTP</option>
      <option value="local">Direct Hugging Face (local CPU/GPU)</option>
      <option value="fake">Fake (offline testing)</option>
    </select></label
  >
  <template v-if="draft.provider_kind === 'openai_compatible'">
    <label
      >Base URL<input
        v-model="draft.base_url"
        :data-testid="`${prefix}-base-url`"
        type="url"
        placeholder="https://provider.example/v1"
        required
    /></label>
    <label
      >Request dialect<select v-model="draft.api_dialect" :data-testid="`${prefix}-api-dialect`">
        <option value="auto">Auto</option>
        <option value="openai">OpenAI</option>
        <option value="deepseek">DeepSeek</option>
        <option value="generic">Generic Chat Completions</option>
      </select></label
    >
    <label
      ><input v-model="draft.local" :data-testid="`${prefix}-local`" type="checkbox" /> Local
      inference server (deployment-allowlisted origin)</label
    >
  </template>
  <label
    >Authentication<select
      v-model="draft.auth_mode"
      :data-testid="`${prefix}-auth-mode`"
      :disabled="draft.provider_kind !== 'openai_compatible'"
    >
      <option value="api_key">Explicit API key pool</option>
      <option v-if="draft.local || draft.provider_kind === 'local'" value="none">
        No authentication (no inherited key)
      </option>
    </select></label
  >
  <label
    ><input v-model="draft.chat_capable" :data-testid="`${prefix}-chat-capable`" type="checkbox" />
    Chat (answers and summaries)</label
  >
  <label v-if="draft.chat_capable"
    >Chat model<input
      v-model="draft.chat_model"
      :data-testid="`${prefix}-chat-model`"
      autocomplete="off"
  /></label>
  <label
    ><input
      v-model="draft.embedding_capable"
      :data-testid="`${prefix}-embedding-capable`"
      type="checkbox"
    />
    Embedding</label
  >
  <label v-if="draft.embedding_capable"
    >Embedding model<input
      v-model="draft.embedding_model"
      :data-testid="`${prefix}-embedding-model`"
      autocomplete="off"
  /></label>
  <label v-if="draft.provider_kind === 'openai_compatible' && draft.chat_capable"
    ><input v-model="draft.reasoning_capable" type="checkbox" /> Supported reasoning-effort
    overrides</label
  >
  <label><input v-model="draft.redact_before_ai" type="checkbox" /> Redact before inference</label>
  <template v-if="draft.provider_kind === 'local'">
    <p>
      Inference stays on this host. Saving does not load or download a model. Only safe model
      weights and deployment-approved model/cache paths are accepted.
    </p>
    <LocalInferenceFields
      v-if="draft.chat_capable"
      v-model="draft.local_chat_options"
      mode="chat"
      :prefix="prefix"
    />
    <LocalInferenceFields
      v-if="draft.embedding_capable"
      v-model="draft.local_embedding_options"
      mode="embedding"
      :prefix="prefix"
    />
  </template>
</template>
