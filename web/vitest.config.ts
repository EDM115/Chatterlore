import { defineVitestConfig } from "@nuxt/test-utils/config";

export default defineVitestConfig({
  test: {
    environment: "nuxt",
    hookTimeout: 90_000,
    include: ["test/**/*.nuxt.spec.ts", "test/**/*.unit.spec.ts"],
  },
});
