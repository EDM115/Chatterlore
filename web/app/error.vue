<script setup lang="ts">
import type { NuxtError } from "#app";

defineProps<{ error: NuxtError }>();

const returnHome = () => clearError({ redirect: "/" });
</script>

<template>
  <UApp>
    <main class="standalone-state">
      <p class="eyebrow">Control plane unavailable</p>
      <h1>That page could not be loaded.</h1>
      <p>Error {{ error.statusCode }}. No sensitive diagnostic detail is shown here.</p>
      <UButton color="primary" @click="returnHome">Return to overview</UButton>
    </main>
  </UApp>
</template>
