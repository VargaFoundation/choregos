import { expect, test } from "@playwright/test";

/**
 * The console's theme (ADR 0043): dark by default, light or the system's at will, and the choice
 * applies BEFORE the first paint — the head script sets it, not React.
 */

const background = (page: import("@playwright/test").Page) =>
  page.evaluate(() => getComputedStyle(document.body).backgroundColor);

test("dark by default, in Geist, monospace kept for code", async ({ page }) => {
  await page.goto("/");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  // slateDark 1: a cold near-black, no longer the former design system's #0A0A0A.
  expect(await background(page)).toBe("rgb(17, 17, 19)");
  expect(await page.evaluate(() => getComputedStyle(document.body).fontFamily)).toMatch(/Geist/);
  expect(await page.evaluate(() => getComputedStyle(document.body).fontFamily)).not.toMatch(/JetBrains/);
});

test("the chosen light theme survives a reload, and applies without the application's JavaScript", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByRole("radio", { name: "Light" }).check({ force: true });
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  expect(await background(page)).toBe("rgb(252, 252, 253)");

  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");

  // Without the application's scripts, only the head script runs: it is the one that sets the theme.
  await page.route(/\/_next\/static\/.*\.js(\?.*)?$/, (route) => route.abort());
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  expect(await background(page)).toBe("rgb(252, 252, 253)");
});

test("\"system\" follows the system's mode", async ({ page }) => {
  await page.emulateMedia({ colorScheme: "dark" });
  await page.goto("/");
  await page.getByRole("radio", { name: "System" }).check({ force: true });
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await page.emulateMedia({ colorScheme: "light" });
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
});
