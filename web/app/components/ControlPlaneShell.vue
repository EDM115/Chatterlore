<script setup lang="ts">
const route = useRoute();
const controlSession = useControlSession();
const hydrated = ref(false);
const mobileMenu = ref<HTMLDetailsElement | null>(null);
const loggingOut = ref(false);
const logoutError = ref<string | null>(null);

onMounted(() => {
  hydrated.value = true;
});

watch(
  () => route.fullPath,
  async () => {
    if (!import.meta.client) return;
    await nextTick();
    document.querySelector<HTMLElement>("#control-plane-content")?.focus({ preventScroll: true });
  },
);

const authenticated = computed(
  () => hydrated.value && controlSession.status.value === "authenticated",
);
const visibleUser = computed(() => (hydrated.value ? controlSession.user.value : null));
const showLogout = computed(
  () => authenticated.value && controlSession.clientKind.value === "browser",
);
const accessLabel = computed(() =>
  hydrated.value ? controlSession.accessLabel.value : "Authentication required",
);

const navigation = computed(() => {
  if (authenticated.value) {
    return [
      { label: "Dashboard", to: "/" },
      { label: "Chats", to: "/chats" },
      { label: "Query", to: "/query" },
      { label: "Memory", to: "/memory" },
      { label: "Stats", to: "/stats" },
      { label: "Audit", to: "/audit" },
      ...(controlSession.hasCapability("bot.admin")
        ? [
            { label: "Operations", to: "/operations" },
            { label: "Bot Settings", to: "/bot-settings" },
            { label: "Providers", to: "/providers" },
            { label: "Embedding indexes", to: "/embedding-indexes" },
          ]
        : []),
    ];
  }
  return [{ label: "Sign in", to: "/login" }];
});

const isCurrent = (to: string) => route.path === to;

function closeMobileMenu() {
  if (mobileMenu.value) mobileMenu.value.open = false;
}

async function logout() {
  if (!showLogout.value || loggingOut.value) return;
  loggingOut.value = true;
  logoutError.value = null;
  try {
    await controlSession.logout();
    closeMobileMenu();
    await navigateTo("/login");
  } catch {
    logoutError.value = "Log out could not be completed. Please retry.";
  } finally {
    loggingOut.value = false;
  }
}
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
        <button
          v-if="showLogout"
          class="navigation-link"
          type="button"
          aria-label="Log out"
          :disabled="loggingOut"
          @click="logout"
        >
          {{ loggingOut ? "Logging out…" : "Log out" }}
        </button>
      </nav>

      <div class="sidebar-note">
        <span class="status-dot" aria-hidden="true" />
        <span>
          <strong>{{ accessLabel }}</strong>
          <small>
            {{
              visibleUser ? `Telegram ${visibleUser.telegram_user_id}` : "Telegram-backed access"
            }}
          </small>
        </span>
      </div>
    </aside>

    <div class="shell-workspace">
      <header class="mobile-header">
        <NuxtLink class="brand-lockup" to="/" aria-label="PZP AI control plane home">
          <span class="brand-mark" aria-hidden="true">PZP</span>
          <strong>Control Plane</strong>
        </NuxtLink>
        <details ref="mobileMenu" class="mobile-menu">
          <summary>Menu</summary>
          <nav aria-label="Mobile navigation">
            <NuxtLink
              v-for="item in navigation"
              :key="item.to"
              class="navigation-link"
              :to="item.to"
              :aria-current="isCurrent(item.to) ? 'page' : undefined"
              @click="closeMobileMenu"
            >
              {{ item.label }}
            </NuxtLink>
            <button
              v-if="showLogout"
              class="navigation-link"
              type="button"
              aria-label="Log out"
              :disabled="loggingOut"
              @click="logout"
            >
              {{ loggingOut ? "Logging out…" : "Log out" }}
            </button>
          </nav>
        </details>
      </header>

      <main id="control-plane-content" class="shell-content" tabindex="-1">
        <p v-if="logoutError" role="alert">{{ logoutError }}</p>
        <slot />
      </main>

      <footer class="shell-footer">
        <span>PZP AI</span>
        <span>Same-origin control plane</span>
      </footer>
    </div>
  </div>
</template>
