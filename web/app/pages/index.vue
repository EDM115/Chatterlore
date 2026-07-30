<script setup lang="ts">
useSeoMeta({
  title: "Overview · PZP AI Control Plane",
});

const foundations = [
  {
    eyebrow: "Identity",
    title: "Telegram-native access",
    description:
      "Browser OIDC and signed Mini App launches will resolve to one revocable server-side session.",
  },
  {
    eyebrow: "Authorization",
    title: "Scope stays explicit",
    description:
      "Bot-wide operations and chat or topic administration remain separate capabilities.",
  },
  {
    eyebrow: "Providers",
    title: "Resilience without exposure",
    description:
      "Managed credential pools will rotate safely while bootstrap secrets stay deployment-only.",
  },
];
</script>

<template>
  <section class="page-introduction" aria-labelledby="overview-title">
    <div>
      <p class="eyebrow">Plan 021 · Foundation</p>
      <h1 id="overview-title">Private operations, one clear surface.</h1>
      <p class="page-lede">
        The control-plane shell is ready for its authenticated API. This phase establishes the
        responsive Nuxt workspace and its reproducible security gates without exposing privileged
        controls early.
      </p>
    </div>
    <UBadge color="neutral" variant="soft" size="lg">Phase 2 preview</UBadge>
  </section>

  <section class="foundation-grid" aria-label="Control-plane foundations">
    <UCard v-for="foundation in foundations" :key="foundation.title" class="foundation-card">
      <p class="eyebrow">{{ foundation.eyebrow }}</p>
      <h2>{{ foundation.title }}</h2>
      <p>{{ foundation.description }}</p>
    </UCard>
  </section>

  <section class="next-step-panel" aria-labelledby="next-step-title">
    <div>
      <p class="eyebrow">Next checkpoint</p>
      <h2 id="next-step-title">Authentication before administration</h2>
      <p>
        No settings, credentials, memory or group actions are available until Telegram identity,
        session and authorization boundaries land in the next approved phase.
      </p>
    </div>
    <UButton to="/login" color="primary" variant="solid">View sign-in readiness</UButton>
  </section>
</template>
