<script setup lang="ts">
useSeoMeta({
  title: "Access denied · Chatterlore Control Plane",
});
</script>

<template>
  <section class="state-page" aria-labelledby="unauthorized-title">
    <p class="eyebrow">Access boundary</p>
    <h1 id="unauthorized-title">This account cannot access that scope.</h1>
    <p>
      Return to the overview. Authorization details will remain intentionally generic on this
      screen.
    </p>
    <UButton to="/" color="neutral" variant="outline">Return to overview</UButton>
  </section>
</template>
