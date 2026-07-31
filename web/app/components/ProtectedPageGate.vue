<script setup lang="ts">
const controlSession = useControlSession();
const hydrated = ref(false);

onMounted(() => {
  hydrated.value = true;
});

const canRenderProtectedContent = computed(
  () => hydrated.value && controlSession.status.value === "authenticated",
);
</script>

<template>
  <slot v-if="canRenderProtectedContent" />
  <section
    v-else
    class="state-page"
    aria-busy="true"
    aria-labelledby="auth-pending-title"
    data-testid="auth-pending"
  >
    <p class="eyebrow">Secure session</p>
    <h1 id="auth-pending-title">Checking Telegram access…</h1>
    <p>
      This protected workspace becomes available after the browser verifies your current
      Telegram-backed session.
    </p>
    <noscript
      >JavaScript is required to complete authentication, but no protected data has been included in
      this document.</noscript
    >
  </section>
</template>
