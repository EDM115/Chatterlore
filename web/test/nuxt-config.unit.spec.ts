// @vitest-environment node

import { afterEach, describe, expect, it, vi } from "vitest";

afterEach(() => {
  vi.unstubAllGlobals();
  delete process.env.PZP_API_DEV_ORIGIN;
  vi.resetModules();
});

describe("Nuxt development proxy", () => {
  it("server-renders the Telegram SDK before client application bootstrap", async () => {
    vi.stubGlobal("defineNuxtConfig", (config: object) => config);

    const { default: config } = await import("../nuxt.config");

    expect(config.app?.head?.script).toContainEqual({
      src: "https://telegram.org/js/telegram-web-app.js?63",
      defer: true,
    });
  });

  it("forwards every reserved backend path to one configurable loopback origin", async () => {
    process.env.PZP_API_DEV_ORIGIN = "http://127.0.0.1:43111";
    vi.stubGlobal("defineNuxtConfig", (config: object) => config);

    const { default: config } = await import("../nuxt.config");

    expect(config.nitro?.devProxy).toEqual({
      "/api/control": {
        target: "http://127.0.0.1:43111/api/control",
        changeOrigin: true,
      },
      "/auth": { target: "http://127.0.0.1:43111/auth", changeOrigin: true },
      "/telegram/webhook": {
        target: "http://127.0.0.1:43111/telegram/webhook",
        changeOrigin: true,
      },
      "/health": { target: "http://127.0.0.1:43111/health", changeOrigin: true },
    });
  });
});
