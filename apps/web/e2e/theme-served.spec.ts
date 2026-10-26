import { expect, test } from "@playwright/test";

/**
 * A theme the API serves is applied before the first paint, inside the console's CSP (S39-03b,
 * ADR 0064). The demo build has no API: the cookie `choregos_mock_theme=sample` makes the layout
 * use a neutral sample theme (never a real brand).
 */
const SAMPLE = { name: "choregos_mock_theme", value: "sample", url: "http://127.0.0.1:3000" };

test.beforeEach(async ({ context, baseURL }) => {
  await context.addCookies([{ ...SAMPLE, url: baseURL ?? SAMPLE.url }]);
});

test("the sample theme changes the page: colours, title font, name, favicon, default mode", async ({ page }) => {
  const violations: string[] = [];
  page.on("console", (message) => {
    if (/Content Security Policy|Refused to/i.test(message.text())) violations.push(message.text());
  });
  const font = page.waitForResponse((response) => response.url().includes("/api/v1/mock-theme-assets/sample-serif.woff2"));

  await page.goto("/");

  // default_mode: light, with no stored preference.
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  await expect(page.locator("html")).toHaveAttribute("data-default-theme", "light");
  expect(await page.evaluate(() => getComputedStyle(document.body).backgroundColor)).toBe("rgb(247, 249, 248)");
  expect(await page.evaluate(() => getComputedStyle(document.querySelector("h1")!).fontFamily)).toMatch(/^"?Sample Serif/);
  expect(await page.evaluate(() => getComputedStyle(document.querySelector("h1")!).fontWeight)).toBe("300");
  // From 1280 px the wordmark sits in the sidebar, below it in the top bar: the visible one.
  await expect(page.getByText("Sample", { exact: true }).locator("visible=true").first()).toBeVisible();
  await expect(page.locator("img[data-testid^=wordmark-logo]").locator("visible=true").first()).toHaveAttribute(
    "src",
    "/api/v1/mock-theme-assets/logo.svg",
  );
  await expect(page.locator('link[rel="icon"]')).toHaveAttribute("href", "/api/v1/mock-theme-assets/favicon.svg");
  await expect(page.getByTestId("footer-text")).toHaveText("Operated by Sample");
  // The attribution stays whatever the theme says (ADR 0048).
  await expect(page.getByTestId("footer-foundation")).toContainText("Varga Foundation");
  expect(await page.title()).toMatch(/· Sample$/);

  expect((await font).status()).toBe(200);
  expect(violations).toEqual([]);
});

test("a dark override never leaks into the light mode, and the reverse", async ({ page }) => {
  await page.goto("/");
  const accent = () => page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue("--choregos-accent").trim());
  expect(await accent()).toBe("#0b6e4f");
  await page.getByRole("radio", { name: "Dark" }).check({ force: true });
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  expect(await accent()).toBe("#1f7a5a");
});

test("the person's own choice still wins over the theme's default mode", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("radio", { name: "Dark" }).check({ force: true });
  // The choice is applied (and stored) before the reload, or the reload can race it (S41-27).
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  expect(await page.evaluate(() => localStorage.getItem("choregos.theme"))).toBe("dark");
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
});
