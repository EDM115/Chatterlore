import { test as base } from "@playwright/test";

export { expect, type Page, type Route } from "@playwright/test";

export const test = base.extend({
  page: async ({ page }, use) => {
    await page.route("https://telegram.org/js/telegram-web-app.js*", (route) =>
      route.fulfill({ body: "", contentType: "application/javascript" }),
    );
    await use(page);
  },
});
