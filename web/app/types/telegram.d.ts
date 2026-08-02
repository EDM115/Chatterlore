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
        BackButton?: {
          hide?: () => void;
          offClick?: (callback: () => void) => void;
          onClick?: (callback: () => void) => void;
          show?: () => void;
        };
        SettingsButton?: {
          hide?: () => void;
          offClick?: (callback: () => void) => void;
          onClick?: (callback: () => void) => void;
          show?: () => void;
        };
        disableClosingConfirmation?: () => void;
        enableClosingConfirmation?: () => void;
        setBackgroundColor?: (color: string) => void;
        setBottomBarColor?: (color: string) => void;
        setHeaderColor?: (color: string) => void;
        ready: () => void;
        expand: () => void;
      };
    };
  }
}
