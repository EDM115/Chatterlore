import { expect, test } from "@playwright/test";

test("keyboard users can skip navigation and focus the main content", async ({ page }) => {
  const browserErrors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") browserErrors.push(message.text());
  });
  page.on("pageerror", (error) => browserErrors.push(error.message));

  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Private operations, one clear surface." }),
  ).toBeVisible();

  expect(browserErrors).toEqual([]);

  await page.keyboard.press("Tab");
  await expect(page.locator(".skip-link")).toBeFocused();
  await page.keyboard.press("Enter");

  await expect(page.locator("#control-plane-content")).toBeFocused();
});

test("foundation routes retain distinct document titles", async ({ page }) => {
  await page.goto("/");
  await expect(page).toHaveTitle("Overview · PZP AI Control Plane");

  await page.goto("/login");
  await expect(page).toHaveTitle("Sign in · PZP AI Control Plane");
});
