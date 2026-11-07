import { TELEGRAM_WEB_APP_SCRIPT } from "~~/telegram";

export const TELEGRAM_WEB_APP_READY_EVENT = "pzp:telegram-web-app-ready";

export const TELEGRAM_WEB_APP_SCRIPT_SELECTOR = `script[src="${TELEGRAM_WEB_APP_SCRIPT}"]`;
const TELEGRAM_WEB_APP_READY_TIMEOUT_MS = 2_000;

export function readTelegramInitData(): string {
  if (import.meta.server) return "";
  return window.Telegram?.WebApp.initData.trim() ?? "";
}

export async function waitForTelegramInitData(): Promise<string> {
  if (import.meta.server) return "";
  const current = readTelegramInitData();
  if (current || window.Telegram?.WebApp) return current;
  if (!hasTelegramLaunchParameters()) return "";
  const script = document.querySelector<HTMLScriptElement>(TELEGRAM_WEB_APP_SCRIPT_SELECTOR);

  return await new Promise<string>((resolve) => {
    let settled = false;
    const finish = () => {
      if (settled) return;
      settled = true;
      window.clearTimeout(timeout);
      window.removeEventListener(TELEGRAM_WEB_APP_READY_EVENT, finish);
      script?.removeEventListener("load", finish);
      script?.removeEventListener("error", finish);
      resolve(readTelegramInitData());
    };
    const timeout = window.setTimeout(finish, TELEGRAM_WEB_APP_READY_TIMEOUT_MS);
    window.addEventListener(TELEGRAM_WEB_APP_READY_EVENT, finish, { once: true });
    script?.addEventListener("load", finish, { once: true });
    script?.addEventListener("error", finish, { once: true });
    queueMicrotask(() => {
      if (window.Telegram?.WebApp) finish();
    });
  });
}

function hasTelegramLaunchParameters(): boolean {
  return /(?:^|[?#&])tgWebApp(?:Data|Version|Platform)=/.test(
    `${window.location.search}${window.location.hash}`,
  );
}
