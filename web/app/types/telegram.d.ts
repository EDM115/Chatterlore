export {};

declare global {
  interface Window {
    Telegram?: {
      WebApp: {
        initData: string;
        colorScheme: "light" | "dark";
        ready: () => void;
        expand: () => void;
      };
    };
  }
}
