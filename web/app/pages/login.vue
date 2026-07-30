<script setup lang="ts">
useSeoMeta({
  title: "Sign in · PZP AI Control Plane",
});
</script>

<template>
  <section class="state-page" aria-labelledby="login-title">
    <p class="eyebrow">Authentication boundary</p>
    <h1 id="login-title">Telegram sign-in is not connected yet.</h1>
    <p>
      Browser OIDC and Mini App launch validation arrive together in Phase 3. Until then, this route
      intentionally performs no authentication and stores no identity data.
    </p>
    <div class="state-actions">
      <UButton disabled color="primary">Sign in with Telegram</UButton>
      <UButton to="/" color="neutral" variant="ghost">Return to overview</UButton>
    </div>
  </section>
</template>
