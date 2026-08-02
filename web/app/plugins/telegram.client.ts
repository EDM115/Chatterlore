export const TELEGRAM_WEB_APP_SCRIPT = "https://telegram.org/js/telegram-web-app.js?63";

interface TelegramButton {
  hide?: () => void;
  offClick?: (callback: () => void) => void;
  onClick?: (callback: () => void) => void;
  show?: () => void;
}

interface TelegramChrome {
  BackButton?: TelegramButton;
  SettingsButton?: TelegramButton;
  ready: () => void;
  expand: () => void;
  setBackgroundColor?: (color: string) => void;
  setBottomBarColor?: (color: string) => void;
  setHeaderColor?: (color: string) => void;
}

interface TelegramClosingConfirmation {
  disableClosingConfirmation?: () => void;
  enableClosingConfirmation?: () => void;
}

interface TelegramChromeOptions {
  canOpenSettings: boolean;
  isRoot: boolean;
  onBack: () => void;
  onSettings: () => void;
}

export function configureTelegramChrome(
  webApp: TelegramChrome,
  options: TelegramChromeOptions,
): () => void {
  webApp.ready();
  webApp.expand();
  webApp.setHeaderColor?.("secondary_bg_color");
  webApp.setBackgroundColor?.("bg_color");
  webApp.setBottomBarColor?.("bottom_bar_bg_color");

  if (options.isRoot) webApp.BackButton?.hide?.();
  else {
    webApp.BackButton?.onClick?.(options.onBack);
    webApp.BackButton?.show?.();
  }
  if (options.canOpenSettings) {
    webApp.SettingsButton?.onClick?.(options.onSettings);
    webApp.SettingsButton?.show?.();
  } else webApp.SettingsButton?.hide?.();

  return () => {
    webApp.BackButton?.offClick?.(options.onBack);
    webApp.SettingsButton?.offClick?.(options.onSettings);
  };
}

export function syncTelegramClosingConfirmation(
  webApp: TelegramClosingConfirmation | undefined,
  dirty: boolean,
): void {
  if (dirty) webApp?.enableClosingConfirmation?.();
  else webApp?.disableClosingConfirmation?.();
}

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

  const stop = watch(() => [route.path, ...controlSession.capabilities.value], prepareTelegram);
  if (window.Telegram?.WebApp) prepareTelegram();

  useHead({
    script: [
      {
        key: "telegram-web-app",
        src: TELEGRAM_WEB_APP_SCRIPT,
        defer: true,
        onload: prepareTelegram,
      },
    ],
  });

  if (import.meta.hot) {
    import.meta.hot.dispose(() => {
      stop();
      cleanup();
    });
  }
});
