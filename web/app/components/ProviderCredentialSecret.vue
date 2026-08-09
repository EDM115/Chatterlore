<script setup lang="ts">
interface CredentialSummary {
  id: number | string;
  profile_name: string;
  last_four: string | null;
  revealable: boolean;
}

const props = defineProps<{ credential: CredentialSummary }>();
const sensitive = useSensitiveReveal();
const error = ref<string | null>(null);
const numericCredentialId = computed(() =>
  typeof props.credential.id === "number" ? props.credential.id : null,
);
const visibleValue = computed(() =>
  sensitive.revealedCredentialId.value === numericCredentialId.value
    ? sensitive.value.value
    : maskedCredential(props.credential.last_four),
);

async function reveal() {
  if (numericCredentialId.value === null || !props.credential.revealable) return;
  error.value = null;
  try {
    await sensitive.reveal(props.credential.profile_name, numericCredentialId.value);
  } catch (caught) {
    error.value = controlErrorMessage(caught, "The credential could not be revealed.");
  }
}

async function copy() {
  error.value = null;
  try {
    if (!(await sensitive.copy())) error.value = "The credential is not currently revealed.";
  } catch (caught) {
    error.value = controlErrorMessage(caught, "The credential could not be copied.");
  }
}

function mask() {
  error.value = null;
  sensitive.clear();
}
</script>

<template>
  <div class="credential-secret">
    <output aria-live="polite">{{ visibleValue }}</output>
    <button
      v-if="!sensitive.revealed.value"
      data-testid="reveal-secret"
      type="button"
      :disabled="!credential.revealable || numericCredentialId === null"
      @click="reveal"
    >
      Reveal
    </button>
    <template v-else>
      <button data-testid="copy-secret" type="button" @click="copy">Copy</button>
      <button data-testid="mask-secret" type="button" @click="mask">Mask</button>
    </template>
    <p v-if="error" role="alert">{{ error }}</p>
  </div>
</template>
