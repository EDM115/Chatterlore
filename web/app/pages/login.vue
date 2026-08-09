<script setup lang="ts">
useSeoMeta({
  title: "Sign in · PZP AI Control Plane",
});

const route = useRoute();
const requestUrl = useRequestURL();
const controlSession = useControlSession();
const sessionExpired = computed(() => route.query.expired === "1");
const returnTo = computed(() =>
  safeReturnTo(route.query.return_to ?? requestUrl.searchParams.get("return_to")),
);
const browserLoginHref = computed(
  () => `/auth/telegram/start?return_to=${encodeURIComponent(returnTo.value)}`,
);
const miniAppLaunch = computed(
  () => import.meta.client && Boolean(window.Telegram?.WebApp.initData.trim()),
);

onMounted(async () => {
  if (await controlSession.bootstrap()) {
    await navigateTo(returnTo.value);
  }
});

function safeReturnTo(value: unknown): string {
  if (
    typeof value !== "string" ||
    value.length > 512 ||
    !value.startsWith("/") ||
    value.startsWith("//") ||
    value.includes("\\") ||
    value.includes("#") ||
    hasControlCharacter(value)
  ) {
    return "/";
  }
  return value;
}

function hasControlCharacter(value: string): boolean {
  return [...value].some((character) => character.charCodeAt(0) < 32);
}
</script>

<template>
  <section class="state-page" aria-labelledby="login-title">
    <p class="eyebrow">Telegram identity</p>
    <div v-if="sessionExpired" class="operation-error" role="alert">
      <strong>Your Telegram session expired.</strong>
      <span>Sign in again to continue. No protected data was retained in this page.</span>
    </div>
    <h1 id="login-title">
      {{ miniAppLaunch ? "Verifying this Mini App launch…" : "Sign in with Telegram." }}
    </h1>
    <p>
      {{
        miniAppLaunch
          ? "Your signed Telegram launch is being exchanged for the same revocable session used by the browser."
          : "Telegram Login opens the official authorization flow and returns here without exposing credentials to this page."
      }}
    </p>
    <p v-if="controlSession.error.value" role="alert">{{ controlSession.error.value }}</p>
    <div class="state-actions">
      <a
        v-if="!miniAppLaunch"
        :href="browserLoginHref"
        class="button-link"
        data-testid="telegram-browser-login"
      >
        Sign in with Telegram
      </a>
      <UButton to="/" color="neutral" variant="ghost">Return to overview</UButton>
    </div>
  </section>
</template>
