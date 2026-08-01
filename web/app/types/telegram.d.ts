export {};

declare global {
  interface Window {
    Telegram?: {
      WebApp: {
        initData: string;
        colorScheme: "light" | "dark";
        version?: string;
        isVersionAtLeast?: (version: string) => boolean;
        requestChat?: (preparedButtonId: string, callback?: (sent: boolean) => void) => void;
        ready: () => void;
        expand: () => void;
      };
    };
  }
}
