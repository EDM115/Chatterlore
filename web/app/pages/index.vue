<script setup lang="ts">
definePageMeta({ middleware: "auth" });

useSeoMeta({
  title: "Overview · PZP AI Control Plane",
});

const foundations = [
  {
    eyebrow: "Identity",
    title: "Telegram-native access",
    description:
      "Browser OIDC and signed Mini App launches resolve to one revocable server-side session.",
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
      "Managed credential pools rotate safely while bootstrap secrets stay deployment-only.",
  },
];
</script>

<template>
  <ProtectedPageGate>
    <section class="page-introduction" aria-labelledby="overview-title">
      <div>
        <p class="eyebrow">Plan 021 · Through Phase 5</p>
        <h1 id="overview-title">Private operations, one clear surface.</h1>
        <p class="page-lede">
          Telegram identity, revocable sessions and provider resilience now share one responsive
          control-plane surface while FastAPI remains the sole authorization and secret authority.
        </p>
      </div>
      <UBadge color="neutral" variant="soft" size="lg">Phase 5 provider resilience</UBadge>
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
        <h2 id="next-step-title">Scoped administration next</h2>
        <p>
          Phase 6 adds bot settings, administrator grants and managed-chat lifecycle. Query, memory,
          statistics, audit and broader operations remain in their reviewed later phases.
        </p>
      </div>
      <UButton to="/providers" color="primary" variant="solid">Review provider resilience</UButton>
    </section>
  </ProtectedPageGate>
</template>
