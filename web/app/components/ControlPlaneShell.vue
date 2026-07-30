<script setup lang="ts">
const route = useRoute();

const navigation = [
  { label: "Overview", to: "/" },
  { label: "Sign in", to: "/login" },
];

const isCurrent = (to: string) => route.path === to;
</script>

<template>
  <a class="skip-link" href="#control-plane-content">Skip to content</a>
  <div class="control-plane-shell">
    <aside class="shell-sidebar">
      <NuxtLink class="brand-lockup" to="/" aria-label="PZP AI control plane home">
        <span class="brand-mark" aria-hidden="true">PZP</span>
        <span>
          <strong>Control Plane</strong>
          <small>Private operations</small>
        </span>
      </NuxtLink>

      <nav class="desktop-navigation" aria-label="Primary navigation">
        <NuxtLink
          v-for="item in navigation"
          :key="item.to"
          class="navigation-link"
          :class="{ 'navigation-link-current': isCurrent(item.to) }"
          :to="item.to"
          :aria-current="isCurrent(item.to) ? 'page' : undefined"
        >
          {{ item.label }}
        </NuxtLink>
      </nav>

      <div class="sidebar-note">
        <span class="status-dot" aria-hidden="true" />
        <span>
          <strong>Foundation mode</strong>
          <small>Authentication arrives in Phase 3</small>
        </span>
      </div>
    </aside>

    <div class="shell-workspace">
      <header class="mobile-header">
        <NuxtLink class="brand-lockup" to="/" aria-label="PZP AI control plane home">
          <span class="brand-mark" aria-hidden="true">PZP</span>
          <strong>Control Plane</strong>
        </NuxtLink>
        <details class="mobile-menu">
          <summary>Menu</summary>
          <nav aria-label="Mobile navigation">
            <NuxtLink
              v-for="item in navigation"
              :key="item.to"
              class="navigation-link"
              :to="item.to"
              :aria-current="isCurrent(item.to) ? 'page' : undefined"
            >
              {{ item.label }}
            </NuxtLink>
          </nav>
        </details>
      </header>

      <main id="control-plane-content" class="shell-content" tabindex="-1">
        <slot />
      </main>

      <footer class="shell-footer">
        <span>PZP AI</span>
        <span>Same-origin control plane</span>
      </footer>
    </div>
  </div>
</template>
