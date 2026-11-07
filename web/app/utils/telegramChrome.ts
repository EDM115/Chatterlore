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
