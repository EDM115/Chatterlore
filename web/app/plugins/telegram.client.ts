import {
  TELEGRAM_WEB_APP_READY_EVENT,
  TELEGRAM_WEB_APP_SCRIPT_SELECTOR,
} from "../utils/telegramWebApp";

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
