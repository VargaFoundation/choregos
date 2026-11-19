import { expect, test } from "@playwright/test";

/**
 * The workflow's flow (S67-29), on the demo's scripted story: tickets rest in their stations, one walks
 * along an edge to the next (the edge lights up), a human wait is amber, a failed run flashes red.
 */
test("the flow: a ticket walks from one station to the next and the edge lights up", async ({ page }) => {
  await page.goto("/p/billing-api/flow");
  await expect(page.getByTestId("live-indicator")).toContainText("LIVE");
  const token = page.locator('[data-token-id="demo-0"]');
  await expect(token).toBeVisible({ timeout: 10_000 });
  await expect(token).toHaveAttribute("data-station", "triage");
  // The story moves it on to Refine about six seconds in; its edge lights up as it goes.
  await expect(page.locator('[data-edge-id="t-triage"][data-lit="true"]')).toHaveCount(1, { timeout: 15_000 });
  await expect(token).toHaveAttribute("data-station", "refine");
  // The Web Animations API did the walking: no animation is left once it is there.
  await expect.poll(() => token.locator(".flow-token-body").evaluate((el) => el.getAnimations().length), { timeout: 5_000 }).toBe(0);
});

test("the flow: a human wait is amber, a failed run is red with its reason, a run in flight rings its token", async ({ page }) => {
  await page.goto("/p/billing-api/flow");
  await expect(page.locator('[data-status="waiting"]').first()).toBeVisible({ timeout: 10_000 });
  await expect(page.locator('[data-status="running"] .flow-ring').first()).toBeVisible({ timeout: 10_000 });
  const failed = page.locator('[data-status="failed"]').first();
  await expect(failed).toBeVisible({ timeout: 15_000 });
  await failed.hover();
  await expect(failed.getByRole("tooltip")).toContainText("timeout");
});

test("the flow is a tab of the workflow and an entry of the project's navigation", async ({ page }) => {
  await page.goto("/p/billing-api/workflows/default-simple/flow");
  await expect(page.getByRole("link", { name: "Flow", exact: true }).first()).toBeVisible();
  await expect(page.getByTestId("flow-view")).toBeVisible();
  await page.goto("/p/billing-api/board");
  await page.getByRole("link", { name: "Flow", exact: true }).click();
  await expect(page).toHaveURL(/\/p\/billing-api\/flow$/);
  await expect(page.getByTestId("flow-view")).toBeVisible();
});

test("the wall: the flow on the whole screen with the title and a clock; Escape leaves it", async ({ page }) => {
  await page.goto("/p/billing-api/flow?wall=1");
  const wall = page.getByTestId("live-wall");
  await expect(wall).toBeVisible();
  await expect(wall.getByRole("heading", { level: 1 })).toContainText("Billing API");
  await expect(wall.getByTestId("live-clock")).toBeVisible();
  await expect(wall.getByTestId("flow-view")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(wall).toHaveCount(0);
});

test("reduced motion: the token jumps, with nothing animated", async ({ browser }) => {
  const context = await browser.newContext({ reducedMotion: "reduce" });
  const page = await context.newPage();
  await page.goto("/p/billing-api/flow");
  const token = page.locator('[data-token-id="demo-0"]');
  await expect(token).toHaveAttribute("data-station", "refine", { timeout: 15_000 });
  expect(await token.locator(".flow-token-body").evaluate((el) => el.getAnimations().length)).toBe(0);
  await context.close();
});
