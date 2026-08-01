const apiDevelopmentOrigin = process.env.PZP_API_DEV_ORIGIN ?? "http://127.0.0.1:8000";
const developmentProxyTarget = (pathname: string) =>
  new URL(pathname, apiDevelopmentOrigin).toString();

export default defineNuxtConfig({
  compatibilityDate: "2026-07-30",
  ssr: true,
  devtools: { enabled: false },
  modules: ["@nuxt/ui"],
  css: ["~/assets/css/main.css"],
  app: {
    head: {
      title: "PZP AI Control Plane",
      htmlAttrs: { lang: "en" },
      meta: [
        {
          name: "description",
          content: "Private administration and memory operations for PZP AI.",
        },
        { name: "color-scheme", content: "dark light" },
        { name: "theme-color", content: "#08131f" },
      ],
    },
  },
  runtimeConfig: {
    public: {
      controlApiBase: "/api/control/v1",
    },
  },
  routeRules: {
    "/_nuxt/**": {
      headers: { "cache-control": "public, max-age=31536000, immutable" },
    },
  },
  nitro: {
    preset: "node-server",
    devProxy: {
      "/api/control": {
        target: developmentProxyTarget("/api/control"),
        changeOrigin: true,
      },
      "/auth": {
        target: developmentProxyTarget("/auth"),
        changeOrigin: true,
      },
      "/telegram/webhook": {
        target: developmentProxyTarget("/telegram/webhook"),
        changeOrigin: true,
      },
      "/health": {
        target: developmentProxyTarget("/health"),
        changeOrigin: true,
      },
    },
  },
});
