export const TELEGRAM_WEB_APP_SCRIPT = "https://telegram.org/js/telegram-web-app.js?63";

export default defineNuxtPlugin(() => {
  const prepareTelegram = () => {
    const webApp = window.Telegram?.WebApp;
    if (!webApp) return;

    document.documentElement.dataset.telegramWebApp = "true";
    webApp.ready();
    webApp.expand();
  };

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
});
