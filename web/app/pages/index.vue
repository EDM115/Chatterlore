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
    eyebrow: "Managed chats",
    title: "Lifecycle without data loss",
    description:
      "Telegram-bound enrollment, pause, detach, leave and forget remain deliberately distinct.",
  },
];
</script>

<template>
  <ProtectedPageGate>
    <section class="page-introduction" aria-labelledby="overview-title">
      <div>
        <p class="eyebrow">Plan 021 · Through Phase 6</p>
        <h1 id="overview-title">Private operations, one clear surface.</h1>
        <p class="page-lede">
          Telegram identity, revocable sessions, provider resilience and scope-correct chat
          administration now share one responsive control-plane surface while FastAPI remains the
          sole authorization and secret authority.
        </p>
      </div>
      <UBadge color="neutral" variant="soft" size="lg">Phase 6 scoped administration</UBadge>
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
        <p class="eyebrow">Current checkpoint</p>
        <h2 id="next-step-title">Settings and chats are live</h2>
        <p>
          Bot owners can manage runtime controls and all groups; delegated and Telegram-native chat
          administrators remain confined to active chats they administer. Query, memory, statistics,
          audit and broader operations remain in their reviewed later phases.
        </p>
      </div>
      <UButton to="/chats" color="primary" variant="solid">Manage Telegram chats</UButton>
    </section>
  </ProtectedPageGate>
</template>
