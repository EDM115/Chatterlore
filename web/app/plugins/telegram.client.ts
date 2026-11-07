export default defineNuxtPlugin(() => {
  const route = useRoute();
  const router = useRouter();
  const controlSession = useControlSession();
  let cleanup: () => void = () => undefined;

  const prepareTelegram = () => {
    const webApp = window.Telegram?.WebApp;
    if (!webApp) return;
    cleanup();
    document.documentElement.dataset.telegramWebApp = "true";
    cleanup = configureTelegramChrome(webApp, {
      canOpenSettings: controlSession.hasCapability("bot.admin"),
      isRoot: route.path === "/",
      onBack: () => router.back(),
      onSettings: () => void navigateTo("/bot-settings"),
    });
  };

  const markTelegramReady = () => {
    prepareTelegram();
    window.dispatchEvent(new Event(TELEGRAM_WEB_APP_READY_EVENT));
  };

  const stop = watch(() => [route.path, ...controlSession.capabilities.value], prepareTelegram);
  const telegramScript = document.querySelector<HTMLScriptElement>(
    TELEGRAM_WEB_APP_SCRIPT_SELECTOR,
  );
  if (window.Telegram?.WebApp) markTelegramReady();
  else {
    telegramScript?.addEventListener("load", markTelegramReady, { once: true });
    telegramScript?.addEventListener("error", markTelegramReady, { once: true });
  }

  if (import.meta.hot) {
    import.meta.hot.dispose(() => {
      stop();
      cleanup();
      telegramScript?.removeEventListener("load", markTelegramReady);
      telegramScript?.removeEventListener("error", markTelegramReady);
    });
  }
});
