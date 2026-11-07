<script setup lang="ts">
import type { LocalInferenceOptions } from "~/types/providerForms";

defineProps<{ mode: "chat" | "embedding"; prefix: string }>();
const options = defineModel<LocalInferenceOptions>({ required: true });
</script>

<template>
  <fieldset class="profile-form">
    <legend>{{ mode === "chat" ? "Embedded text generation" : "Embedded embeddings" }}</legend>
    <label>Pinned revision<input v-model="options.revision" autocomplete="off" /></label>
    <label v-if="mode === 'chat'"
      >Tokenizer revision<input v-model="options.tokenizer_revision" autocomplete="off"
    /></label>
    <label
      >Device<input
        v-model="options.device"
        :data-testid="`${prefix}-${mode}-device`"
        placeholder="auto, cpu, cuda:0"
        pattern="auto|cpu|cuda:[0-9]+"
    /></label>
    <label v-if="mode === 'chat'"
      >Dtype<select v-model="options.dtype">
        <option value="auto">Auto</option>
        <option value="float32">Float32</option>
        <option value="float16">Float16</option>
        <option value="bfloat16">BFloat16</option>
      </select></label
    >
    <label
      >Cache directory<input
        v-model="options.cache_dir"
        placeholder="Deployment-approved cache root"
        autocomplete="off"
    /></label>
    <label
      ><input v-model="options.local_files_only" type="checkbox" /> Local files only (no model
      download)</label
    >
    <template v-if="mode === 'chat'">
      <label
        >Maximum input tokens<input v-model.number="options.max_input_tokens" type="number" min="1"
      /></label>
      <label
        >Maximum generated tokens<input
          v-model.number="options.max_new_tokens"
          :data-testid="`${prefix}-chat-max-new-tokens`"
          type="number"
          min="1"
      /></label>
      <label
        >Top P<input v-model.number="options.top_p" type="number" min="0.01" max="1" step="0.01"
      /></label>
      <label
        >Queue timeout (seconds)<input
          v-model.number="options.queue_timeout_seconds"
          type="number"
          min="1"
      /></label>
      <label
        >Inference timeout (seconds)<input
          v-model.number="options.inference_timeout_seconds"
          type="number"
          min="1"
      /></label>
    </template>
    <template v-else>
      <label>Batch size<input v-model.number="options.batch_size" type="number" min="1" /></label>
      <label
        ><input v-model="options.normalize_embeddings" type="checkbox" /> Normalize
        embeddings</label
      >
      <label
        >Truncation dimensions<input
          v-model.number="options.truncate_dim"
          type="number"
          min="1"
          placeholder="Model default"
      /></label>
      <label
        >Prompt policy<select v-model="options.prompt_mode">
          <option value="generic">Generic (existing MiniLM behavior)</option>
          <option value="query_document">Query/document-specific</option>
        </select></label
      >
    </template>
  </fieldset>
</template>
